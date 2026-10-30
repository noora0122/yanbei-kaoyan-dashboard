"""
研备 · 考研备考任务与数据管理平台（课程示意项目）

运行方式：
    py -m pip install -r requirements.txt
    py app.py

浏览器访问：
    http://127.0.0.1:5000

首次启动会自动创建 kaoyan.db，并写入一套“以今天为基准”的示例数据。
"""

import json
import os
import re
import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path

from flask import (
    Flask,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

# ---------------------------------------------------------------------------
# 基础配置
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
# 支持通过环境变量指定测试数据库，默认使用项目目录下的 kaoyan.db
DB_PATH = Path(os.environ.get("YANBEI_DB", str(BASE_DIR / "kaoyan.db")))

STAGES = ["基础期", "强化期", "冲刺期", "模考期"]
DEFAULT_SUBJECTS = ["数学", "英语", "政治", "专业课"]
WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
SUBJECT_SPLIT_RE = re.compile(r"[,，、;；\n\r]+")

app = Flask(__name__)
# 演示项目固定密钥；正式部署请从环境变量读取随机值
app.secret_key = "yanbei-demo-secret-key"


# ---------------------------------------------------------------------------
# 数据库连接与初始化
# ---------------------------------------------------------------------------
def get_db():
    """获取当前请求的数据库连接（同一请求内复用）。"""
    if "db" not in g:
        g.db = sqlite3.connect(str(DB_PATH))
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    """请求结束后关闭连接。"""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def get_setting(db, key, default=None):
    """读取 settings 表中的单个配置。"""
    row = db.execute(
        "SELECT value FROM settings WHERE key = ?", (key,)
    ).fetchone()
    return row["value"] if row is not None else default


def set_setting(db, key, value):
    """写入或覆盖 settings 表中的配置。"""
    db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (key, str(value)),
    )


def ensure_schema(db):
    """建表，并把旧版 checkins（duration_min）迁移为 manual_minutes。"""
    db.executescript(
        """
        -- 学习计划：科目和任务均为自由文本，适配任意专业
        CREATE TABLE IF NOT EXISTS plans (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            subject   TEXT    NOT NULL,
            task      TEXT    NOT NULL,
            plan_date TEXT    NOT NULL,              -- YYYY-MM-DD
            done      INTEGER NOT NULL DEFAULT 0     -- 0 未完成 / 1 已完成
        );

        -- 全局配置：key-value，便于后续扩展
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        -- 番茄专注记录：只追加，不提供删除/修改接口
        CREATE TABLE IF NOT EXISTS pomodoro_sessions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id      INTEGER,
            plan_task    TEXT    NOT NULL DEFAULT '',  -- 任务快照
            subject      TEXT    NOT NULL,
            session_date TEXT    NOT NULL,             -- 按结束时间的本地日期
            started_at   TEXT    NOT NULL,
            ended_at     TEXT    NOT NULL,
            duration_min INTEGER NOT NULL,
            status       TEXT    NOT NULL DEFAULT 'completed', -- completed / partial
            created_at   TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
        );

        CREATE INDEX IF NOT EXISTS idx_plans_date ON plans(plan_date);
        CREATE INDEX IF NOT EXISTS idx_sessions_date ON pomodoro_sessions(session_date);
        CREATE INDEX IF NOT EXISTS idx_sessions_plan ON pomodoro_sessions(plan_id);
        """
    )

    has_checkins = db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'checkins'"
    ).fetchone()

    if has_checkins is None:
        # 新库直接创建新版结构
        db.execute(
            """
            CREATE TABLE checkins (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                check_date     TEXT    NOT NULL UNIQUE,
                manual_minutes INTEGER NOT NULL DEFAULT 0,
                note           TEXT    NOT NULL DEFAULT ''
            )
            """
        )
    else:
        columns = {
            row["name"]
            for row in db.execute("PRAGMA table_info(checkins)").fetchall()
        }
        if "manual_minutes" not in columns:
            # 旧库迁移：把原来的 duration_min 视为手动补录，保留历史数据
            db.executescript(
                """
                ALTER TABLE checkins RENAME TO checkins_old;
                CREATE TABLE checkins (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    check_date     TEXT    NOT NULL UNIQUE,
                    manual_minutes INTEGER NOT NULL DEFAULT 0,
                    note           TEXT    NOT NULL DEFAULT ''
                );
                INSERT INTO checkins (id, check_date, manual_minutes, note)
                SELECT id, check_date, duration_min, note FROM checkins_old;
                DROP TABLE checkins_old;
                """
            )

    db.commit()


def init_db():
    """建表、写入默认配置，并在首次启动时生成示例数据。"""
    db = get_db()
    ensure_schema(db)

    exam_date = default_exam_date()
    defaults = {
        "exam_date": exam_date.isoformat(),
        "stage": suggest_stage(exam_date),
        "daily_target": "360",
        "subjects": json.dumps(DEFAULT_SUBJECTS, ensure_ascii=False),
        "setup_done": "0",
        "demo_seeded": "0",
        # 番茄钟默认配置
        "focus_minutes": "25",
        "short_break_minutes": "5",
        "long_break_minutes": "15",
        "long_break_every": "4",
    }
    for key, value in defaults.items():
        # 已存在的配置不会被覆盖，用户修改在重启后仍然保留
        db.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )

    # 只在第一次初始化时写入示例数据
    if get_setting(db, "demo_seeded", "0") != "1":
        seed_demo_data(db)
        set_setting(db, "demo_seeded", "1")

    db.commit()


def seed_demo_data(db):
    """生成一套相对“今天”的示例数据，保证任何时候打开看板都是活的。"""
    today = date.today()

    plan_samples = [
        (-3, "数学", "高数强化：中值定理专题", 1),
        (-2, "英语", "2019 阅读精读", 1),
        (-1, "专业课", "真题 2023 第二遍复盘", 1),
        (0, "数学", "真题二刷：级数与微分方程", 0),
        (0, "英语", "阅读 2 篇 + 长难句整理", 0),
        (0, "政治", "背诵手册：马原第 3 章", 0),
        (0, "数学", "错题回炉：中值定理", 1),
        (1, "政治", "肖八选择题一套", 0),
        (2, "数学", "模拟卷一套（限时 3 小时）", 0),
    ]
    plan_ids = {}
    for offset, subject, task, done in plan_samples:
        plan_date = (today + timedelta(days=offset)).isoformat()
        cursor = db.execute(
            "INSERT INTO plans (subject, task, plan_date, done) "
            "VALUES (?, ?, ?, ?)",
            (subject, task, plan_date, done),
        )
        plan_ids[(offset, subject, task)] = cursor.lastrowid

    # 过去 6 天：番茄专注 + 少量手动补录；今天留空，方便现场演示
    day_samples = [
        (6, 3, 60, "线下补笔记"),
        (5, 4, 45, "单词本 + 长难句"),
        (4, 5, 60, "真题复盘"),
        (3, 3, 30, "整理框架"),
        (2, 6, 60, "模拟卷复盘"),
        (1, 4, 50, "作文模板"),
    ]
    subject_cycle = ["数学", "英语", "政治", "专业课"]
    hours = [9, 10, 14, 15, 16, 19]

    for days_ago, count, manual_minutes, note in day_samples:
        day = today - timedelta(days=days_ago)

        # 当天若有一条种子计划，把第一个番茄关联上去，展示计划投入
        linked_plan = None
        for (offset, p_subject, p_task), pid in plan_ids.items():
            if offset == -days_ago:
                linked_plan = (pid, p_subject, p_task)
                break

        for index in range(count):
            if index == 0 and linked_plan:
                plan_id, subject, plan_task = linked_plan
            else:
                plan_id = None
                subject = subject_cycle[(days_ago + index) % len(subject_cycle)]
                plan_task = ""

            started = datetime.combine(day, time(hours[index % len(hours)], 0))
            ended = started + timedelta(minutes=25)
            db.execute(
                "INSERT INTO pomodoro_sessions "
                "(plan_id, plan_task, subject, session_date, started_at, "
                " ended_at, duration_min, status) "
                "VALUES (?, ?, ?, ?, ?, ?, 25, 'completed')",
                (
                    plan_id,
                    plan_task,
                    subject,
                    day.isoformat(),
                    started.isoformat(timespec="seconds"),
                    ended.isoformat(timespec="seconds"),
                ),
            )

        db.execute(
            "INSERT INTO checkins (check_date, manual_minutes, note) "
            "VALUES (?, ?, ?)",
            (day.isoformat(), manual_minutes, note),
        )

    db.commit()


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------
def parse_iso_date(value):
    """把 YYYY-MM-DD 解析为 date；非法输入返回 None。"""
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except (AttributeError, ValueError):
        return None


def parse_iso_datetime(value):
    """解析前端传来的 ISO 时间；带时区时转换为服务器本地时间。"""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def parse_bounded_int(raw, low, high):
    """把表单值解析为闭区间内的整数；非法或越界返回 None。"""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if low <= value <= high else None


def default_exam_date(today=None):
    """默认考试日期：下一个 12 月 26 日（今天已过则顺延一年）。"""
    today = today or date.today()
    candidate = date(today.year, 12, 26)
    if candidate <= today:
        candidate = date(today.year + 1, 12, 26)
    return candidate


def suggest_stage(exam_date, today=None):
    """根据剩余天数给一个阶段默认值，用户可自行修改。"""
    today = today or date.today()
    days = (exam_date - today).days
    if days > 240:
        return "基础期"
    if days > 120:
        return "强化期"
    if days > 30:
        return "冲刺期"
    return "模考期"


def read_subjects(db):
    """读取科目列表；缺失或损坏时回退到默认科目。"""
    raw = get_setting(db, "subjects", "")
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            cleaned = [str(item).strip() for item in data if str(item).strip()]
            if cleaned:
                return cleaned
    except (TypeError, json.JSONDecodeError):
        pass
    return list(DEFAULT_SUBJECTS)


def save_subjects(db, subjects):
    """科目列表以 JSON 字符串存入 settings，便于自由扩展。"""
    set_setting(db, "subjects", json.dumps(subjects, ensure_ascii=False))


def parse_subjects(raw):
    """支持中英文逗号、顿号、分号、换行分隔，去重并限制长度。"""
    names = []
    for chunk in SUBJECT_SPLIT_RE.split(raw or ""):
        name = chunk.strip()
        if name and name not in names:
            names.append(name[:20])
    return names[:12]


def read_exam_date(db):
    """读取考试日期；非法值回退到默认日期。"""
    parsed = parse_iso_date(get_setting(db, "exam_date", ""))
    return parsed or default_exam_date()


def load_focus_config(db):
    """读取番茄钟配置，并做范围兜底。"""
    return {
        "focus_minutes": parse_bounded_int(
            get_setting(db, "focus_minutes", "25"), 1, 120
        )
        or 25,
        "short_break_minutes": parse_bounded_int(
            get_setting(db, "short_break_minutes", "5"), 1, 60
        )
        or 5,
        "long_break_minutes": parse_bounded_int(
            get_setting(db, "long_break_minutes", "15"), 1, 120
        )
        or 15,
        "long_break_every": parse_bounded_int(
            get_setting(db, "long_break_every", "4"), 1, 12
        )
        or 4,
    }


def load_profile(db):
    """把设置表整理成模板和业务逻辑都能直接使用的字典。"""
    try:
        daily_target = int(get_setting(db, "daily_target", "360"))
    except (TypeError, ValueError):
        daily_target = 360
    daily_target = max(30, min(1440, daily_target))

    stage = get_setting(db, "stage", "冲刺期")
    if stage not in STAGES:
        stage = "冲刺期"

    profile = {
        "exam_date": read_exam_date(db),
        "stage": stage,
        "daily_target": daily_target,
        "subjects": read_subjects(db),
        "setup_done": get_setting(db, "setup_done", "0") == "1",
    }
    profile.update(load_focus_config(db))
    return profile


def today_summary(db, day):
    """某天的番茄 + 手动补录汇总。"""
    session = db.execute(
        "SELECT "
        "SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_count, "
        "COALESCE(SUM(duration_min), 0) AS pomodoro_minutes "
        "FROM pomodoro_sessions WHERE session_date = ?",
        (day.isoformat(),),
    ).fetchone()
    manual_row = db.execute(
        "SELECT manual_minutes FROM checkins WHERE check_date = ?",
        (day.isoformat(),),
    ).fetchone()

    pomodoro_minutes = session["pomodoro_minutes"] or 0
    manual_minutes = manual_row["manual_minutes"] if manual_row else 0
    return {
        "completed_count": session["completed_count"] or 0,
        "pomodoro_minutes": pomodoro_minutes,
        "manual_minutes": manual_minutes,
        "total_minutes": pomodoro_minutes + manual_minutes,
    }


def calc_streak(db, today):
    """
    连续打卡天数：番茄记录或手动补录任一存在即算当天有记录。
    今天有记录从今天往前数；今天没有则从昨天往前数。
    """
    rows = db.execute(
        "SELECT session_date AS day FROM pomodoro_sessions WHERE duration_min > 0 "
        "UNION "
        "SELECT check_date AS day FROM checkins WHERE manual_minutes > 0"
    ).fetchall()
    checkin_days = {parse_iso_date(row["day"]) for row in rows}
    checkin_days.discard(None)

    cursor = today if today in checkin_days else today - timedelta(days=1)
    streak = 0
    while cursor in checkin_days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def build_week(db, today, daily_target):
    """构造近 7 天数据：番茄分钟、手动分钟、总分钟与堆叠柱高度。"""
    start = today - timedelta(days=6)

    session_rows = db.execute(
        "SELECT session_date, COALESCE(SUM(duration_min), 0) AS minutes "
        "FROM pomodoro_sessions WHERE session_date BETWEEN ? AND ? "
        "GROUP BY session_date",
        (start.isoformat(), today.isoformat()),
    ).fetchall()
    manual_rows = db.execute(
        "SELECT check_date, manual_minutes, note FROM checkins "
        "WHERE check_date BETWEEN ? AND ?",
        (start.isoformat(), today.isoformat()),
    ).fetchall()

    pomodoro_by_date = {row["session_date"]: row["minutes"] for row in session_rows}
    manual_by_date = {
        row["check_date"]: (row["manual_minutes"], row["note"])
        for row in manual_rows
    }

    week = []
    for offset in range(6, -1, -1):
        day = today - timedelta(days=offset)
        key = day.isoformat()
        pomodoro = pomodoro_by_date.get(key, 0)
        manual, note = manual_by_date.get(key, (0, ""))
        total = pomodoro + manual
        week.append(
            {
                "date": key,
                "date_label": day.strftime("%m-%d"),
                "label": WEEKDAYS[day.weekday()],
                "pomodoro_minutes": pomodoro,
                "manual_minutes": manual,
                "minutes": total,
                "note": note,
                "is_today": day == today,
            }
        )

    # 纵轴上限至少包含每日目标，保证目标线位置稳定
    scale = max([item["minutes"] for item in week] + [daily_target, 60])
    for item in week:
        item["height"] = round(item["minutes"] * 100 / scale, 1)
        if item["minutes"] > 0:
            item["pomodoro_pct"] = round(
                item["pomodoro_minutes"] * 100 / item["minutes"], 1
            )
            item["manual_pct"] = round(100 - item["pomodoro_pct"], 1)
        else:
            item["pomodoro_pct"] = 0
            item["manual_pct"] = 0

    target_line = min(100, round(daily_target * 100 / scale, 1))
    return week, target_line


def build_subject_stats(db, configured_subjects):
    """按科目统计计划完成情况，配置科目排在前，额外科目自动追加。"""
    rows = db.execute(
        "SELECT subject, COUNT(*) AS total, COALESCE(SUM(done), 0) AS done "
        "FROM plans GROUP BY subject"
    ).fetchall()
    stats = {
        row["subject"]: {"total": row["total"], "done": row["done"]}
        for row in rows
    }

    order = list(configured_subjects)
    for name in stats:
        if name not in order:
            order.append(name)

    result = []
    for name in order:
        data = stats.get(name, {"total": 0, "done": 0})
        total = data["total"]
        done = data["done"]
        result.append(
            {
                "name": name,
                "total": total,
                "done": done,
                "pct": round(done * 100 / total) if total else 0,
            }
        )
    return result


def load_plan_focus(db):
    """每个计划累计的完成番茄数与专注分钟数。"""
    rows = db.execute(
        "SELECT plan_id, "
        "SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_count, "
        "COALESCE(SUM(duration_min), 0) AS minutes "
        "FROM pomodoro_sessions WHERE plan_id IS NOT NULL GROUP BY plan_id"
    ).fetchall()
    return {
        row["plan_id"]: {
            "count": row["completed_count"] or 0,
            "minutes": row["minutes"] or 0,
        }
        for row in rows
    }


def decorate_plan(row, today, focus_map):
    """给计划补充展示用的日期文案、逾期状态和番茄投入。"""
    plan_date = parse_iso_date(row["plan_date"]) or today
    delta = (plan_date - today).days
    done = bool(row["done"])

    if not done and delta < 0:
        label = "已逾期 " + plan_date.strftime("%m-%d")
    elif delta == 0:
        label = "今天"
    elif delta == 1:
        label = "明天"
    elif delta == -1:
        label = "昨天"
    else:
        label = plan_date.strftime("%m-%d")

    focus = focus_map.get(row["id"], {"count": 0, "minutes": 0})
    return {
        "id": row["id"],
        "subject": row["subject"],
        "task": row["task"],
        "done": done,
        "date_label": label,
        "overdue": (not done and delta < 0),
        "focus_count": focus["count"],
        "focus_minutes": focus["minutes"],
    }


def redirect_back(default_endpoint):
    """按表单 next 参数回到来源页；只允许站内路径。"""
    next_url = request.form.get("next", "").strip()
    if next_url.startswith("/") and not next_url.startswith("//"):
        return redirect(next_url)
    return redirect(url_for(default_endpoint))


# ---------------------------------------------------------------------------
# 全局模板变量：番茄钟配置（所有页面悬浮计时器都需要）
# ---------------------------------------------------------------------------
@app.context_processor
def inject_focus_config():
    return {"focus_config": load_focus_config(get_db())}


# ---------------------------------------------------------------------------
# 首次访问引导：完成初始设置前，只允许访问设置页
# ---------------------------------------------------------------------------
@app.before_request
def require_setup():
    if request.endpoint is None:
        return None
    if request.endpoint in {"settings", "save_settings", "static"}:
        return None
    db = get_db()
    if get_setting(db, "setup_done", "0") != "1":
        return redirect(url_for("settings"))
    return None


# ---------------------------------------------------------------------------
# 仪表盘
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    db = get_db()
    profile = load_profile(db)
    today = date.today()

    exam_date = profile["exam_date"]
    days_left = (exam_date - today).days

    focus_map = load_plan_focus(db)
    today_rows = db.execute(
        "SELECT * FROM plans WHERE plan_date = ? ORDER BY done ASC, id ASC",
        (today.isoformat(),),
    ).fetchall()
    today_plans = []
    for row in today_rows:
        item = dict(row)
        focus = focus_map.get(row["id"], {"count": 0, "minutes": 0})
        item["focus_count"] = focus["count"]
        item["focus_minutes"] = focus["minutes"]
        today_plans.append(item)

    today_total = len(today_plans)
    today_done = sum(1 for item in today_plans if item["done"])

    totals = db.execute(
        "SELECT COUNT(*) AS total, COALESCE(SUM(done), 0) AS done FROM plans"
    ).fetchone()
    total_plans = totals["total"]
    done_plans = totals["done"]
    completion_rate = round(done_plans * 100 / total_plans) if total_plans else 0

    summary = today_summary(db, today)
    today_checkin = db.execute(
        "SELECT * FROM checkins WHERE check_date = ?", (today.isoformat(),)
    ).fetchone()

    daily_target = profile["daily_target"]
    today_minutes = summary["total_minutes"]
    today_pct = (
        min(100, round(today_minutes * 100 / daily_target)) if daily_target else 0
    )
    streak = calc_streak(db, today)
    week, target_line = build_week(db, today, daily_target)
    week_total = sum(day["minutes"] for day in week)
    week_recent = list(reversed(week))

    return render_template(
        "dashboard.html",
        profile=profile,
        today_cn=today.strftime("%Y 年 %m 月 %d 日"),
        weekday_cn=WEEKDAYS[today.weekday()],
        exam_date=exam_date,
        days_left=days_left,
        today_plans=today_plans,
        today_total=today_total,
        today_done=today_done,
        total_plans=total_plans,
        done_plans=done_plans,
        completion_rate=completion_rate,
        today_checkin=today_checkin,
        today_minutes=today_minutes,
        today_pomodoro_minutes=summary["pomodoro_minutes"],
        today_pomodoro_count=summary["completed_count"],
        today_manual=summary["manual_minutes"],
        has_today_record=today_minutes > 0,
        today_pct=today_pct,
        daily_target=daily_target,
        streak=streak,
        week=week,
        week_recent=week_recent,
        target_line=target_line,
        week_total=week_total,
        week_avg=round(week_total / 7),
        subject_stats=build_subject_stats(db, profile["subjects"]),
    )


@app.post("/checkin")
def save_checkin():
    """手动补录：同一天重复提交时更新当天的手动分钟数。"""
    today_str = date.today().isoformat()
    raw_minutes = request.form.get("manual_minutes", "").strip()
    note = request.form.get("note", "").strip()

    try:
        minutes = int(raw_minutes)
    except ValueError:
        flash("手动补录时长必须是整数分钟。", "error")
        return redirect(url_for("index"))

    if not 0 <= minutes <= 1440:
        flash("手动补录需在 0 到 1440 分钟之间。", "error")
        return redirect(url_for("index"))

    db = get_db()
    existing = db.execute(
        "SELECT id FROM checkins WHERE check_date = ?", (today_str,)
    ).fetchone()

    if existing is not None:
        db.execute(
            "UPDATE checkins SET manual_minutes = ?, note = ? WHERE id = ?",
            (minutes, note, existing["id"]),
        )
        message = "今日手动补录已更新。"
    else:
        db.execute(
            "INSERT INTO checkins (check_date, manual_minutes, note) "
            "VALUES (?, ?, ?)",
            (today_str, minutes, note),
        )
        message = "今日手动补录已保存。"

    db.commit()
    flash(message, "success")
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# 番茄钟 API（只追加记录，不提供删除/编辑）
# ---------------------------------------------------------------------------
@app.post("/api/pomodoro/sessions")
def create_pomodoro_session():
    payload = request.get_json(silent=True) or {}

    duration = parse_bounded_int(payload.get("duration_min"), 1, 240)
    if duration is None:
        return jsonify({"ok": False, "error": "学习时长不合法。"}), 400

    status = str(payload.get("status", "completed")).strip()
    if status not in {"completed", "partial"}:
        return jsonify({"ok": False, "error": "记录状态不合法。"}), 400

    ended_at = parse_iso_datetime(payload.get("ended_at")) or datetime.now()
    started_at = parse_iso_datetime(payload.get("started_at")) or (
        ended_at - timedelta(minutes=duration)
    )
    session_date = ended_at.date().isoformat()

    subject = str(payload.get("subject", "")).strip()[:20] or "未分类"
    plan_task = str(payload.get("plan_task", "")).strip()[:200]

    plan_id = payload.get("plan_id")
    if plan_id in ("", None, 0, "0"):
        plan_id = None
    else:
        try:
            plan_id = int(plan_id)
        except (TypeError, ValueError):
            plan_id = None

    db = get_db()
    if plan_id is not None:
        plan = db.execute(
            "SELECT id, subject, task FROM plans WHERE id = ?", (plan_id,)
        ).fetchone()
        if plan is not None:
            # 用数据库中的计划信息做快照，避免前端篡改展示内容
            subject = plan["subject"]
            plan_task = plan["task"]
        else:
            # 计划可能已被删除：保留前端传来的快照，但不保留失效外键
            plan_id = None

    db.execute(
        "INSERT INTO pomodoro_sessions "
        "(plan_id, plan_task, subject, session_date, started_at, ended_at, "
        " duration_min, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            plan_id,
            plan_task,
            subject,
            session_date,
            started_at.isoformat(timespec="seconds"),
            ended_at.isoformat(timespec="seconds"),
            duration,
            status,
        ),
    )
    db.commit()

    summary = today_summary(db, date.today())
    summary["streak"] = calc_streak(db, date.today())
    return jsonify(
        {
            "ok": True,
            "session_date": session_date,
            "summary": summary,
        }
    )


# ---------------------------------------------------------------------------
# 学习计划
# ---------------------------------------------------------------------------
@app.get("/plans")
def plans():
    db = get_db()
    profile = load_profile(db)
    today = date.today()
    focus_map = load_plan_focus(db)

    rows = db.execute(
        "SELECT * FROM plans ORDER BY done ASC, plan_date ASC, id ASC"
    ).fetchall()
    pending = [
        decorate_plan(row, today, focus_map) for row in rows if not row["done"]
    ]
    finished = [
        decorate_plan(row, today, focus_map) for row in rows if row["done"]
    ]

    return render_template(
        "plans.html",
        pending=pending,
        finished=finished,
        subjects=profile["subjects"],
        today=today.isoformat(),
        total=len(rows),
    )


@app.post("/plans/add")
def add_plan():
    subject = request.form.get("subject", "").strip()
    task = request.form.get("task", "").strip()
    plan_date = parse_iso_date(request.form.get("plan_date", ""))

    if not subject or not task:
        flash("科目和任务内容不能为空。", "error")
        return redirect(url_for("plans"))
    if plan_date is None:
        flash("计划日期格式不正确。", "error")
        return redirect(url_for("plans"))
    if len(subject) > 20 or len(task) > 200:
        flash("科目最多 20 字，任务最多 200 字。", "error")
        return redirect(url_for("plans"))

    db = get_db()
    # 新科目自动加入设置，科目进度卡会同步出现
    subjects = read_subjects(db)
    if subject not in subjects:
        subjects.append(subject)
        save_subjects(db, subjects)

    db.execute(
        "INSERT INTO plans (subject, task, plan_date, done) "
        "VALUES (?, ?, ?, 0)",
        (subject, task, plan_date.isoformat()),
    )
    db.commit()
    flash("学习计划已添加。", "success")
    return redirect(url_for("plans"))


@app.post("/plans/<int:plan_id>/toggle")
def toggle_plan(plan_id):
    db = get_db()
    cursor = db.execute(
        "UPDATE plans SET done = 1 - done WHERE id = ?", (plan_id,)
    )
    db.commit()
    if cursor.rowcount == 0:
        flash("该计划不存在。", "error")
    return redirect_back("plans")


@app.post("/plans/<int:plan_id>/delete")
def delete_plan(plan_id):
    db = get_db()
    cursor = db.execute("DELETE FROM plans WHERE id = ?", (plan_id,))
    db.commit()
    if cursor.rowcount == 0:
        flash("该计划不存在。", "error")
    else:
        flash("学习计划已删除。", "success")
    return redirect(url_for("plans"))


# ---------------------------------------------------------------------------
# 专注历史（基础版：按月份查看；年/月/日下钻留到第二轮）
# ---------------------------------------------------------------------------
@app.get("/focus")
def focus_history():
    db = get_db()
    today = date.today()
    month_raw = request.args.get("month", "").strip()

    try:
        month_start = datetime.strptime(month_raw, "%Y-%m").date().replace(day=1)
    except ValueError:
        month_start = today.replace(day=1)

    next_month = (month_start + timedelta(days=32)).replace(day=1)
    start_str = month_start.isoformat()
    end_str = next_month.isoformat()

    sessions = db.execute(
        "SELECT * FROM pomodoro_sessions "
        "WHERE session_date >= ? AND session_date < ? "
        "ORDER BY started_at DESC",
        (start_str, end_str),
    ).fetchall()
    checkins = db.execute(
        "SELECT * FROM checkins WHERE check_date >= ? AND check_date < ?",
        (start_str, end_str),
    ).fetchall()

    days = {}
    for row in sessions:
        key = row["session_date"]
        day = days.setdefault(
            key,
            {"date": key, "sessions": [], "manual_minutes": 0, "note": ""},
        )
        start_dt = parse_iso_datetime(row["started_at"])
        end_dt = parse_iso_datetime(row["ended_at"])
        time_range = (
            f"{start_dt.strftime('%H:%M')} – {end_dt.strftime('%H:%M')}"
            if start_dt and end_dt
            else "—"
        )
        day["sessions"].append(
            {
                "time_range": time_range,
                "subject": row["subject"],
                "plan_task": row["plan_task"],
                "duration_min": row["duration_min"],
                "status": row["status"],
            }
        )

    for row in checkins:
        key = row["check_date"]
        day = days.setdefault(
            key,
            {"date": key, "sessions": [], "manual_minutes": 0, "note": ""},
        )
        day["manual_minutes"] = row["manual_minutes"]
        day["note"] = row["note"]

    day_list = []
    for key in sorted(days, reverse=True):
        item = days[key]
        parsed = parse_iso_date(key)
        item["date_label"] = parsed.strftime("%m 月 %d 日") if parsed else key
        item["weekday"] = WEEKDAYS[parsed.weekday()] if parsed else ""
        item["pomodoro_minutes"] = sum(
            session["duration_min"] for session in item["sessions"]
        )
        item["pomodoro_count"] = sum(
            1 for session in item["sessions"] if session["status"] == "completed"
        )
        item["total_minutes"] = item["pomodoro_minutes"] + item["manual_minutes"]
        day_list.append(item)

    completed_count = sum(1 for row in sessions if row["status"] == "completed")
    focus_minutes = sum(row["duration_min"] for row in sessions)
    manual_minutes = sum(row["manual_minutes"] for row in checkins)

    return render_template(
        "focus_history.html",
        day_list=day_list,
        month_value=month_start.strftime("%Y-%m"),
        month_label=month_start.strftime("%Y 年 %m 月"),
        prev_month=(month_start - timedelta(days=1)).strftime("%Y-%m"),
        next_month=next_month.strftime("%Y-%m"),
        completed_count=completed_count,
        focus_minutes=focus_minutes,
        manual_minutes=manual_minutes,
        total_minutes=focus_minutes + manual_minutes,
        active_days=sum(1 for item in day_list if item["total_minutes"] > 0),
        today=today.isoformat(),
    )


# ---------------------------------------------------------------------------
# 设置 / 首次引导
# ---------------------------------------------------------------------------
@app.get("/settings")
def settings():
    db = get_db()
    profile = load_profile(db)
    counts = db.execute(
        "SELECT "
        "(SELECT COUNT(*) FROM plans) AS plans, "
        "(SELECT COUNT(*) FROM checkins) AS checkins, "
        "(SELECT COUNT(*) FROM pomodoro_sessions) AS sessions"
    ).fetchone()

    return render_template(
        "settings.html",
        profile=profile,
        stages=STAGES,
        subjects_text="，".join(profile["subjects"]),
        plan_count=counts["plans"],
        checkin_count=counts["checkins"],
        session_count=counts["sessions"],
    )


@app.post("/settings")
def save_settings():
    exam_date = parse_iso_date(request.form.get("exam_date", ""))
    stage = request.form.get("stage", "").strip()
    raw_target = request.form.get("daily_target", "").strip()
    subjects = parse_subjects(request.form.get("subjects", ""))

    focus_minutes = parse_bounded_int(
        request.form.get("focus_minutes"), 1, 120
    )
    short_break = parse_bounded_int(
        request.form.get("short_break_minutes"), 1, 60
    )
    long_break = parse_bounded_int(
        request.form.get("long_break_minutes"), 1, 120
    )
    long_every = parse_bounded_int(
        request.form.get("long_break_every"), 1, 12
    )

    if exam_date is None:
        flash("考试日期格式不正确，请重新选择。", "error")
        return redirect(url_for("settings"))
    if stage not in STAGES:
        stage = "冲刺期"
    try:
        daily_target = int(raw_target)
    except ValueError:
        daily_target = 0
    if not 30 <= daily_target <= 1440:
        flash("每日目标建议在 30 到 1440 分钟之间。", "error")
        return redirect(url_for("settings"))
    if not subjects:
        flash("至少填写一个科目。", "error")
        return redirect(url_for("settings"))
    if None in (focus_minutes, short_break, long_break, long_every):
        flash("番茄钟时长设置不正确，请检查后再保存。", "error")
        return redirect(url_for("settings"))

    db = get_db()
    first_time = get_setting(db, "setup_done", "0") != "1"
    set_setting(db, "exam_date", exam_date.isoformat())
    set_setting(db, "stage", stage)
    set_setting(db, "daily_target", daily_target)
    save_subjects(db, subjects)
    set_setting(db, "focus_minutes", focus_minutes)
    set_setting(db, "short_break_minutes", short_break)
    set_setting(db, "long_break_minutes", long_break)
    set_setting(db, "long_break_every", long_every)
    set_setting(db, "setup_done", "1")
    db.commit()

    flash(
        "设置已保存，开始今天的备考吧！" if first_time else "设置已更新。",
        "success",
    )
    return redirect(url_for("index"))


@app.post("/demo/reset")
def reset_demo():
    """清空计划、补录与番茄记录，重新生成示例数据（保留当前设置）。"""
    db = get_db()
    db.execute("DELETE FROM pomodoro_sessions")
    db.execute("DELETE FROM plans")
    db.execute("DELETE FROM checkins")
    seed_demo_data(db)
    flash("示例数据已重置（仅演示用）。", "success")
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------
with app.app_context():
    init_db()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
