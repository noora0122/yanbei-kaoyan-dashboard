/**
 * 研备 · 番茄钟前端逻辑
 *
 * 设计原则：
 * 1. 计时状态存在 localStorage，切换页面/刷新后可以恢复；
 * 2. 完成或提前结束时，把“这一轮”POST 到后端，后端只追加记录；
 * 3. 休息不计入学习时长，只作为一个可选的倒计时；
 * 4. 同一浏览器同一时间只维护一个计时器。
 */
(() => {
  'use strict';

  const widget = document.getElementById('focus-widget');
  if (!widget) return;

  const body = document.body;
  const cfg = {
    focus: toInt(body.dataset.focusMinutes, 25),
    shortBreak: toInt(body.dataset.shortBreak, 5),
    longBreak: toInt(body.dataset.longBreak, 15),
    longEvery: toInt(body.dataset.longEvery, 4),
  };

  const STORAGE_KEY = 'yanbei_focus_state_v1';
  const CYCLE_KEY = 'yanbei_focus_cycle_v1';

  const ui = {
    ring: document.getElementById('focus-ring'),
    time: document.getElementById('focus-time'),
    mode: document.getElementById('focus-mode'),
    meta: document.getElementById('focus-meta'),
    actions: document.getElementById('focus-actions'),
    complete: document.getElementById('focus-complete'),
    completeText: document.getElementById('focus-complete-text'),
    primary: document.getElementById('focus-primary'),
    secondary: document.getElementById('focus-secondary'),
    pause: document.getElementById('focus-pause'),
    stop: document.getElementById('focus-stop'),
    form: document.getElementById('focus-start-form'),
    planSelect: document.getElementById('focus-plan'),
    subjectWrap: document.getElementById('focus-subject-wrap'),
    subjectSelect: document.getElementById('focus-subject'),
    activeNote: document.getElementById('focus-active-note'),
    startHint: document.getElementById('focus-start-hint'),
  };

  const originalTitle = document.title;
  let state = loadState();
  let cycleCount = loadCycle();
  let tickHandle = null;
  let audioCtx = null;
  let completionVisible = false;
  let busy = false;
  let titleFlashHandle = null;
  let titleFlashTimeout = null;
  let isFlashing = false;

  // -------------------------------------------------------------------------
  // 小工具
  // -------------------------------------------------------------------------
  function toInt(value, fallback) {
    const parsed = parseInt(value, 10);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function formatTime(ms) {
    const totalSeconds = Math.max(0, Math.ceil(ms / 1000));
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
  }

  function getRemaining(targetState) {
    if (targetState.paused) return Math.max(0, targetState.remainingMs || 0);
    return Math.max(0, targetState.endsAt - Date.now());
  }

  function getProgress(targetState) {
    const total = targetState.totalMs || targetState.durationMin * 60000;
    if (!total) return 0;
    const remain = getRemaining(targetState);
    return Math.min(100, Math.max(0, ((total - remain) * 100) / total));
  }

  function setText(id, value) {
    const node = document.getElementById(id);
    if (node) node.textContent = String(value);
  }

  // -------------------------------------------------------------------------
  // 本地状态
  // -------------------------------------------------------------------------
  function loadState() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      if (!parsed || !['focus', 'shortBreak', 'longBreak'].includes(parsed.mode)) {
        return null;
      }
      if (
        typeof parsed.durationMin !== 'number' ||
        parsed.durationMin <= 0 ||
        typeof parsed.endsAt !== 'number'
      ) {
        return null;
      }
      parsed.startedAt =
        typeof parsed.startedAt === 'number' ? parsed.startedAt : Date.now();
      parsed.totalMs = parsed.durationMin * 60000;
      parsed.paused = Boolean(parsed.paused);
      parsed.remainingMs =
        typeof parsed.remainingMs === 'number'
          ? Math.max(0, parsed.remainingMs)
          : parsed.totalMs;
      parsed.planId = parsed.planId || null;
      parsed.subject = parsed.subject || '未分类';
      parsed.planTask = parsed.planTask || '';
      return parsed;
    } catch (error) {
      return null;
    }
  }

  function saveState() {
    if (!state) return;
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch (error) {
      /* 隐私模式下 localStorage 可能不可用，忽略 */
    }
  }

  function clearState() {
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch (error) {
      /* ignore */
    }
  }

  function loadCycle() {
    try {
      const value = toInt(localStorage.getItem(CYCLE_KEY), 0);
      return value >= 0 ? value : 0;
    } catch (error) {
      return 0;
    }
  }

  function saveCycle() {
    try {
      localStorage.setItem(CYCLE_KEY, String(cycleCount));
    } catch (error) {
      /* ignore */
    }
  }

  // -------------------------------------------------------------------------
  // 界面渲染
  // -------------------------------------------------------------------------
  function modeLabel(targetState) {
    if (targetState.paused) return '已暂停';
    if (targetState.mode === 'focus') return '专注中';
    if (targetState.mode === 'longBreak') return '长休中';
    return '短休中';
  }

  function setFormActive(active) {
    if (!ui.form) return;
    ui.form.hidden = active;
    if (ui.activeNote) ui.activeNote.hidden = !active;
    if (ui.startHint) ui.startHint.hidden = active;
  }

  function updateSubjectVisibility() {
    if (!ui.planSelect || !ui.subjectWrap) return;
    ui.subjectWrap.hidden = Boolean(ui.planSelect.value);
  }

  function updateDisplay() {
    if (!state) return;
    const remaining = getRemaining(state);
    ui.time.textContent = formatTime(remaining);
    ui.ring.style.setProperty('--progress', getProgress(state).toFixed(1));

    if (!isFlashing) {
      const icon = state.paused ? '⏸' : state.mode === 'focus' ? '🍅' : '☕';
      document.title = `${icon} ${formatTime(remaining)} · ${modeLabel(state)}`;
    }
  }

  function renderActive() {
    if (!state) return;
    clearTitleFlash();
    completionVisible = false;
    widget.hidden = false;
    widget.classList.toggle('is-break', state.mode !== 'focus');
    ui.complete.hidden = true;
    ui.actions.hidden = false;
    ui.completeText.textContent = '';
    ui.primary.onclick = null;
    ui.secondary.onclick = null;
    ui.mode.textContent = modeLabel(state);
    ui.meta.textContent =
      state.mode === 'focus'
        ? `${state.subject}${state.planTask ? ' · ' + state.planTask : ''}`
        : '休息一下，不计入学习时长';
    ui.pause.textContent = state.paused ? '继续' : '暂停';
    setFormActive(true);
    updateDisplay();
    startTick();
  }

  function renderIdle() {
    if (state || completionVisible) return;
    clearTitleFlash();
    stopTick();
    widget.hidden = true;
    widget.classList.remove('is-break');
    ui.time.textContent = '00:00';
    ui.ring.style.setProperty('--progress', '0');
    document.title = originalTitle;
    setFormActive(false);
  }

  function showCompletion(options) {
    const {
      isBreak = false,
      modeLabel: title,
      meta = '',
      text,
      primaryLabel,
      secondaryLabel,
      onPrimary,
      onSecondary,
    } = options;

    completionVisible = true;
    stopTick();
    widget.hidden = false;
    widget.classList.toggle('is-break', Boolean(isBreak));
    ui.time.textContent = '00:00';
    ui.ring.style.setProperty('--progress', '100');
    ui.mode.textContent = title;
    ui.meta.textContent = meta;
    ui.actions.hidden = true;
    ui.complete.hidden = false;
    ui.completeText.textContent = text;
    ui.primary.textContent = primaryLabel;
    ui.secondary.textContent = secondaryLabel;
    ui.primary.onclick = onPrimary;
    ui.secondary.onclick = onSecondary;
    setFormActive(true);
    document.title = `${title} · 研备`;
  }

  function hideCompletion() {
    completionVisible = false;
    ui.complete.hidden = true;
    ui.actions.hidden = false;
    ui.completeText.textContent = '';
    ui.primary.onclick = null;
    ui.secondary.onclick = null;
    if (state) {
      renderActive();
    } else {
      renderIdle();
    }
  }

  // -------------------------------------------------------------------------
  // 计时循环
  // -------------------------------------------------------------------------
  function startTick() {
    if (tickHandle) return;
    tickHandle = setInterval(tick, 500);
    tick();
  }

  function stopTick() {
    if (tickHandle) {
      clearInterval(tickHandle);
      tickHandle = null;
    }
  }

  function tick() {
    if (!state) return;
    updateDisplay();
    if (state.paused || busy) return;
    if (state.endsAt <= Date.now()) {
      if (state.mode === 'focus') {
        handleFocusComplete(state, false);
      } else {
        handleBreakComplete(state);
      }
    }
  }

  // -------------------------------------------------------------------------
  // 开始 / 暂停 / 结束
  // -------------------------------------------------------------------------
  function startFocus(planId, subject, planTask) {
    if (state || busy || completionVisible) return;
    ensureAudio();
    const minutes = cfg.focus;
    const total = minutes * 60000;
    const now = Date.now();
    state = {
      mode: 'focus',
      durationMin: minutes,
      totalMs: total,
      startedAt: now,
      endsAt: now + total,
      paused: false,
      remainingMs: total,
      planId: planId || null,
      subject: subject || '未分类',
      planTask: planTask || '',
    };
    saveState();
    clearTitleFlash();
    renderActive();
  }

  function startBreak(kind, minutes, focusState) {
    if (state || busy) return;
    ensureAudio();
    const total = minutes * 60000;
    const now = Date.now();
    state = {
      mode: kind === 'long' ? 'longBreak' : 'shortBreak',
      durationMin: minutes,
      totalMs: total,
      startedAt: now,
      endsAt: now + total,
      paused: false,
      remainingMs: total,
      planId: focusState.planId || null,
      subject: focusState.subject || '未分类',
      planTask: focusState.planTask || '',
    };
    saveState();
    clearTitleFlash();
    renderActive();
  }

  function togglePause() {
    if (!state) return;
    ensureAudio();
    if (state.paused) {
      state.paused = false;
      state.endsAt = Date.now() + Math.max(0, state.remainingMs || 0);
    } else {
      state.remainingMs = Math.max(0, state.endsAt - Date.now());
      state.paused = true;
    }
    saveState();
    renderActive();
  }

  function stopTimer() {
    if (!state) return;
    const current = state;

    if (current.mode !== 'focus') {
      if (!window.confirm('结束本次休息？')) return;
      state = null;
      clearState();
      stopTick();
      renderIdle();
      return;
    }

    const elapsed = current.totalMs - getRemaining(current);
    const minutes = Math.floor(elapsed / 60000);

    if (minutes < 1) {
      if (!window.confirm('本次专注不足 1 分钟，不会记录。确定结束吗？')) return;
      state = null;
      clearState();
      stopTick();
      renderIdle();
      return;
    }

    if (!window.confirm(`已专注 ${minutes} 分钟，提前结束会按实际时长记录，确定吗？`)) {
      return;
    }

    const payload = buildPayload(current, 'partial', minutes, new Date());
    state = null;
    clearState();
    stopTick();
    attemptRecord(payload, current, { partial: true });
  }

  // -------------------------------------------------------------------------
  // 完成处理
  // -------------------------------------------------------------------------
  function handleFocusComplete(focusState, recovered) {
    if (busy) return;
    const endedAt = recovered ? new Date(focusState.endsAt) : new Date();
    const payload = buildPayload(
      focusState,
      'completed',
      focusState.durationMin,
      endedAt
    );
    state = null;
    clearState();
    stopTick();
    attemptRecord(payload, focusState, {});
  }

  function handleBreakComplete(breakState) {
    state = null;
    clearState();
    stopTick();
    beep();
    flashTitle('☕ 休息结束');
    showCompletion({
      isBreak: true,
      modeLabel: '休息结束',
      meta: breakState.mode === 'longBreak' ? '长休结束' : '短休结束',
      text: '开始下一轮专注吗？',
      primaryLabel: '开始下一轮',
      secondaryLabel: '结束',
      onPrimary: () => {
        hideCompletion();
        startFocus(
          breakState.planId,
          breakState.subject,
          breakState.planTask
        );
      },
      onSecondary: () => {
        hideCompletion();
        renderIdle();
      },
    });
  }

  function showExpiredFocus(focusState) {
    const meta = `${focusState.subject}${
      focusState.planTask ? ' · ' + focusState.planTask : ''
    }`;
    showCompletion({
      isBreak: false,
      modeLabel: '上一轮已到时间',
      meta,
      text: `上一轮计划 ${focusState.durationMin} 分钟，是否记录为完成？`,
      primaryLabel: '记录完成',
      secondaryLabel: '放弃',
      onPrimary: () => {
        const payload = buildPayload(
          focusState,
          'completed',
          focusState.durationMin,
          new Date(focusState.endsAt)
        );
        state = null;
        clearState();
        stopTick();
        hideCompletion();
        attemptRecord(payload, focusState, {});
      },
      onSecondary: () => {
        state = null;
        clearState();
        stopTick();
        hideCompletion();
        renderIdle();
      },
    });
  }

  // -------------------------------------------------------------------------
  // 记录提交
  // -------------------------------------------------------------------------
  function buildPayload(focusState, status, duration, endedAt) {
    return {
      plan_id: focusState.planId || null,
      subject: focusState.subject || '未分类',
      plan_task: focusState.planTask || '',
      duration_min: duration,
      status,
      started_at: new Date(focusState.startedAt).toISOString(),
      ended_at: endedAt.toISOString(),
    };
  }

  async function recordSession(payload) {
    const response = await fetch('/api/pomodoro/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) {
      throw new Error(data.error || `HTTP ${response.status}`);
    }
    return data.summary;
  }

  function attemptRecord(payload, focusState, options = {}) {
    if (busy) return;
    busy = true;

    recordSession(payload)
      .then((summary) => {
        busy = false;
        refreshToday(summary);

        if (options.partial) {
          showPartialDone(payload.duration_min, focusState);
          return;
        }

        beep();
        flashTitle('✅ 专注完成');
        const kind = advanceCycle();
        showBreakOffer(kind, focusState);
      })
      .catch((error) => {
        busy = false;
        showCompletion({
          isBreak: false,
          modeLabel: '记录失败',
          meta: '',
          text: '本轮记录失败：' + error.message,
          primaryLabel: '重试',
          secondaryLabel: '关闭',
          onPrimary: () => {
            hideCompletion();
            attemptRecord(payload, focusState, options);
          },
          onSecondary: () => {
            hideCompletion();
            renderIdle();
          },
        });
      });
  }

  function advanceCycle() {
    cycleCount += 1;
    if (cycleCount >= cfg.longEvery) {
      cycleCount = 0;
      saveCycle();
      return 'long';
    }
    saveCycle();
    return 'short';
  }

  function showBreakOffer(kind, focusState) {
    const minutes = kind === 'long' ? cfg.longBreak : cfg.shortBreak;
    const label = kind === 'long' ? '长休' : '短休';
    showCompletion({
      isBreak: false,
      modeLabel: '本轮专注完成',
      meta: `已记录 ${focusState.durationMin} 分钟`,
      text: `要开始 ${minutes} 分钟${label}吗？`,
      primaryLabel: `开始${label}`,
      secondaryLabel: '跳过',
      onPrimary: () => {
        hideCompletion();
        startBreak(kind, minutes, focusState);
      },
      onSecondary: () => {
        hideCompletion();
        renderIdle();
      },
    });
  }

  function showPartialDone(minutes, focusState) {
    showCompletion({
      isBreak: false,
      modeLabel: '已提前结束',
      meta: `已记录 ${minutes} 分钟`,
      text: '本轮标记为未完成，分钟数已计入今日学习。',
      primaryLabel: '知道了',
      secondaryLabel: '再来一轮',
      onPrimary: () => {
        hideCompletion();
        renderIdle();
      },
      onSecondary: () => {
        hideCompletion();
        startFocus(focusState.planId, focusState.subject, focusState.planTask);
      },
    });
  }

  // 局部刷新仪表盘上的今日学习数字；其他页面没有这些节点，函数会自动跳过
  function refreshToday(summary) {
    const card = document.getElementById('today-card');
    if (!card || !summary) return;

    const target = toInt(card.dataset.dailyTarget, 360);
    const total = Number(summary.total_minutes) || 0;
    const pomodoro = Number(summary.pomodoro_minutes) || 0;
    const count = Number(summary.completed_count) || 0;
    const manual = Number(summary.manual_minutes) || 0;
    const percent = target
      ? Math.min(100, Math.round((total * 100) / target))
      : 0;

    setText('today-total-minutes', total);
    setText('today-pomodoro-minutes', pomodoro);
    setText('today-pomodoro-count', count);
    setText('today-manual-minutes', manual);

    const bar = document.getElementById('today-progress-bar');
    if (bar) bar.style.width = `${percent}%`;

    const hint = document.getElementById('today-hint');
    if (hint) {
      if (percent >= 100) {
        hint.textContent = '今日目标已达成，保持节奏。';
      } else if (total > 0) {
        hint.textContent = `还差 ${Math.max(0, target - total)} 分钟达标。`;
      } else {
        hint.textContent = '今天还没记录，先开始一个番茄吧。';
      }
    }

    if (summary.streak !== undefined) {
      setText('streak-number', summary.streak);
    }
    if (total > 0) {
      const streakHint = document.getElementById('streak-hint');
      if (streakHint) streakHint.textContent = '今天已有学习记录，继续保持。';
    }
  }

  // -------------------------------------------------------------------------
  // 声音与标题
  // -------------------------------------------------------------------------
  function ensureAudio() {
    try {
      if (!audioCtx) {
        const AudioContextClass =
          window.AudioContext || window.webkitAudioContext;
        if (AudioContextClass) audioCtx = new AudioContextClass();
      }
      if (audioCtx && audioCtx.state === 'suspended') {
        audioCtx.resume();
      }
    } catch (error) {
      /* 浏览器不支持时静默降级 */
    }
  }

  function beep() {
    if (!audioCtx) return;
    try {
      if (audioCtx.state === 'suspended') audioCtx.resume();
      const now = audioCtx.currentTime;
      [
        { offset: 0, frequency: 880 },
        { offset: 0.18, frequency: 1174 },
      ].forEach(({ offset, frequency }) => {
        const oscillator = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        oscillator.type = 'sine';
        oscillator.frequency.value = frequency;
        gain.gain.setValueAtTime(0.0001, now + offset);
        gain.gain.exponentialRampToValueAtTime(0.18, now + offset + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, now + offset + 0.15);
        oscillator.connect(gain).connect(audioCtx.destination);
        oscillator.start(now + offset);
        oscillator.stop(now + offset + 0.17);
      });
    } catch (error) {
      /* ignore */
    }
  }

  function clearTitleFlash() {
    clearInterval(titleFlashHandle);
    clearTimeout(titleFlashTimeout);
    titleFlashHandle = null;
    titleFlashTimeout = null;
    isFlashing = false;
  }

  function flashTitle(message) {
    clearTitleFlash();
    isFlashing = true;
    let showMessage = false;
    titleFlashHandle = setInterval(() => {
      document.title = showMessage ? message : originalTitle;
      showMessage = !showMessage;
    }, 700);
    titleFlashTimeout = setTimeout(() => {
      clearInterval(titleFlashHandle);
      titleFlashHandle = null;
      isFlashing = false;
      if (state) {
        updateDisplay();
      } else {
        document.title = originalTitle;
      }
    }, 8000);
  }

  // -------------------------------------------------------------------------
  // 表单与初始化
  // -------------------------------------------------------------------------
  function onStartSubmit(event) {
    event.preventDefault();
    if (state || busy || completionVisible) return;

    let planId = null;
    let subject = '';
    let planTask = '';

    if (ui.planSelect && ui.planSelect.value) {
      planId = toInt(ui.planSelect.value, null);
      const option = ui.planSelect.options[ui.planSelect.selectedIndex];
      subject = (option && option.dataset.subject) || '';
      planTask = (option && option.dataset.task) || '';
    } else if (ui.subjectSelect) {
      subject = ui.subjectSelect.value;
    }

    if (!subject) {
      window.alert('请先选择科目。');
      return;
    }

    startFocus(planId, subject, planTask);
  }

  function init() {
    if (ui.planSelect) {
      ui.planSelect.addEventListener('change', updateSubjectVisibility);
      updateSubjectVisibility();
    }
    if (ui.form) {
      ui.form.addEventListener('submit', onStartSubmit);
    }
    if (ui.pause) ui.pause.addEventListener('click', togglePause);
    if (ui.stop) ui.stop.addEventListener('click', stopTimer);

    if (!state) {
      renderIdle();
      return;
    }

    if (state.paused) {
      renderActive();
      return;
    }

    if (state.endsAt <= Date.now()) {
      if (state.mode === 'focus') {
        showExpiredFocus(state);
      } else {
        handleBreakComplete(state);
      }
      return;
    }

    renderActive();
  }

  init();
})();
