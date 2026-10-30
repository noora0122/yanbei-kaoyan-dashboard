# 研备 · 开发交接记录

> 给下一次继续开发的人（或 AI）看的项目状态快照。  
> 快照日期：2026-10-30  
> 版本：v0.1.0（课程示意版）  
> 项目路径：`D:\code-demo`

---

## 1. 现在做到哪了

已完成：

- Flask + SQLite 单文件后端，首次运行自动建库、建表、写入示例数据
- 30 秒初始设置：考试日期、阶段、每日目标、科目自由输入、番茄钟时长
- 仪表盘：倒计时、今日学习（🍅 番茄 + ✍️ 手动补录）、连续打卡、计划完成率、近 7 天堆叠趋势、科目进度、今日任务、开始专注、手动补录、最近 7 天记录
- 番茄钟：默认 25/5，每 4 个长休 15 分钟；可配置；暂停/继续；提前结束按实际分钟记录（`partial`）；结束提示音 + 标题提示；全局悬浮计时器；`localStorage` 跨页面/刷新恢复
- 计划关联：可选今日计划或自由专注；计划显示累计 `🍅 数量 · 分钟`；完成番茄不自动完成计划
- 专注记录页：按月查看每次番茄与手动补录；历史只读
- 旧数据库自动迁移：`checkins.duration_min` → `manual_minutes`，历史不丢
- README + 看板截图

尚未完成（下一轮）：

- 专注记录的年 / 月 / 日三级下钻与日历热力图（当前只有按月）
- 浏览器通知（当前只有提示音 + 标题闪烁）
- 多标签页互斥 / 服务端计时（当前计时在浏览器本地）
- 登录、多用户、云端同步、CSRF（当前是单用户本地演示）
- 错题本、自动计划生成、院校/科目模板库

---

## 2. 快速启动

```bash
cd D:\code-demo
py -m pip install -r requirements.txt
py app.py
```

打开 <http://127.0.0.1:5000>。

- 首次访问会进入 `/settings` 初始设置页，示例数据已预置
- `kaoyan.db` 首次运行自动生成，已在 `.gitignore` 中
- 想彻底重置：停止服务，删除 `kaoyan.db`，重新运行
- 设置页底部「重置示例数据（仅演示）」会清空计划、补录、番茄记录并重新生成示例数据（保留设置）

测试时可用环境变量指定独立数据库，避免污染本地演示库：

```bash
# Windows Git Bash 示例
YANBEI_DB="D:/code-demo/_test.db" py app.py
```

---

## 3. 架构地图

### `app.py`

单文件后端，主要区块：

| 区块 | 说明 |
| --- | --- |
| `get_db` / `close_db` | 请求级 SQLite 连接，`row_factory = sqlite3.Row` |
| `ensure_schema` | 建表；旧 `checkins` 表迁移 |
| `init_db` | 默认配置 + 首次示例数据 |
| `seed_demo_data` | 相对今天生成计划、番茄、手动补录 |
| `load_profile` / `load_focus_config` | 设置读取与范围兜底 |
| `today_summary` | 某天番茄 + 手动汇总 |
| `calc_streak` | 番茄或手动任一存在即算当天有记录 |
| `build_week` | 7 天番茄/手动/总计、堆叠柱高度、目标线 |
| `load_plan_focus` | 每个计划的累计番茄数/分钟 |
| 路由 | 见下表 |

路由：

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/` | 仪表盘 |
| POST | `/checkin` | 手动补录（一天一条，更新当天） |
| POST | `/api/pomodoro/sessions` | 追加番茄记录（JSON） |
| GET | `/plans` | 计划列表 |
| POST | `/plans/add` | 添加计划 |
| POST | `/plans/<id>/toggle` | 完成/撤销 |
| POST | `/plans/<id>/delete` | 删除计划 |
| GET | `/focus` | 专注记录（按月，`?month=YYYY-MM`） |
| GET | `/settings` | 初始设置 / 平台设置 |
| POST | `/settings` | 保存设置（含番茄配置） |
| POST | `/demo/reset` | 重置演示数据（仅演示用） |

### `static/app.js`

番茄钟前端逻辑，无框架。关键点：

- 状态存 `localStorage` 键：`yanbei_focus_state_v1`
- 长休周期计数键：`yanbei_focus_cycle_v1`
- 状态字段：`mode`（focus / shortBreak / longBreak）、`durationMin`、`totalMs`、`startedAt`、`endsAt`、`paused`、`remainingMs`、`planId`、`subject`、`planTask`
- 计时用绝对时间 `endsAt` 计算，避免标签页节流导致漂移
- 完成/提前结束 → `POST /api/pomodoro/sessions` → 局部更新仪表盘今日卡片
- 刷新/切页后根据 `endsAt` 恢复；若已超时：
  - 专注：先弹确认，不静默记账
  - 休息：直接进入“休息结束”
- 休息不写入数据库；长休由本地周期计数决定
- 同一时间只维护一个计时器；多标签页未做互斥

### 模板

- `base.html`：全局样式、导航、悬浮计时器、`focus_config` data 属性
- `dashboard.html`：四个统计卡 + 堆叠趋势 + 开始专注 + 今日任务 + 手动补录 + 最近 7 天
- `plans.html`：添加计划、待完成/已完成分组
- `settings.html`：目标、科目、番茄时长、演示数据重置
- `focus_history.html`：按月专注记录，历史只读

---

## 4. 数据模型与计算口径

### 表

- `plans(id, subject, task, plan_date, done)`
- `checkins(id, check_date UNIQUE, manual_minutes, note)` —— 每日**手动补录**
- `pomodoro_sessions(id, plan_id, plan_task, subject, session_date, started_at, ended_at, duration_min, status, created_at)` —— 番茄记录，只追加
- `settings(key, value)` —— 考试日期、阶段、每日目标、科目 JSON、番茄配置、初始化标记

### 关键口径

```text
今日总时长 = SUM(当天 pomodoro_sessions.duration_min)
           + 当天 checkins.manual_minutes
```

- `status = completed`：正常完成；计入“完成番茄个数”
- `status = partial`：提前结束；分钟数计入总时长，但不计入完成番茄个数
- 休息不写记录、不计入时长
- 连续打卡：番茄记录或手动补录任一存在即算当天有记录
- 计划被删除后，番茄记录保留 `plan_task` / `subject` 快照，`plan_id` 可能失效
- 手动补录一天一条；再次提交更新当天，不覆盖番茄

### 示例数据

`seed_demo_data` 生成过去 6 天的计划/番茄/手动补录，今天留空，方便现场演示。  
番茄记录共 25 条（3+4+5+3+6+4），其中部分与 -3/-2/-1 天的计划关联。

---

## 5. 番茄钟状态机（重要）

```text
IDLE
  └─ 开始专注 → FOCUS(endsAt)
        ├─ 暂停 → PAUSED(remainingMs) → 继续 → FOCUS
        ├─ 结束（<1 分钟）→ IDLE
        ├─ 结束（>=1 分钟）→ POST partial → 完成面板 → IDLE / 再来一轮
        └─ 倒计时到 0 → POST completed → 休息询问
              ├─ 开始休息 → SHORT/LONG BREAK(endsAt)
              │     └─ 倒计时到 0 → 休息结束面板 → 下一轮 / 结束
              └─ 跳过 → IDLE
```

边界行为：

- 页面关闭期间到点：重新打开时先询问“是否记录为完成”
- `localStorage` 清空 = 放弃当前计时器；已提交的番茄记录不受影响
- 修改设置里的专注时长不会影响已经开始的那一轮
- 多标签页同时打开时可能重复恢复同一状态；未做锁

---

## 6. 关键设计决策（不要轻易改）

1. 番茄记录只追加，不提供删除/编辑；纠错放后续版本（例如“作废标记”而不是物理删除）
2. 手动补录是每日一个值，与番茄相加，不覆盖番茄
3. 番茄完成不自动完成计划
4. 休息不进入学习时长和历史
5. 计时器优先浏览器本地，不引入服务端轮询；除非明确要求关掉浏览器也继续计时
6. 专注历史当前按月，年/月/日下钻是下一轮
7. 示例数据相对今天生成，不要写死日期
8. 旧库迁移逻辑在 `ensure_schema`，改 `checkins` 结构时保留迁移

---

## 7. 测试记录

已跑过：

- Flask test client：首次引导、设置、番茄 API（完成/部分）、手动补录、页面拆分、历史页、设置校验、重置、旧库迁移
- 真实 Edge + Playwright：开始 1 分钟番茄 → 暂停/继续 → 切页恢复 → 自动完成 → 今日学习更新 → 跳过休息 → 历史页展示 → 数据库校验
- 全部通过；浏览器无 console error

如果下一轮要补自动化测试，建议：

```bash
py -m pip install pytest playwright
# Playwright 使用已安装的 Edge：p.chromium.launch(channel="msedge")
```

测试要点：使用 `YANBEI_DB` 指向临时库；不要动真实的 `kaoyan.db`。

---

## 8. Git / 交付状态

- 本地已初始化 git 仓库，主分支 `main`
- 课程示意版提交后打 tag：`v0.1.0`
- 远程仓库尚未配置（等待 GitHub 用户名 / 仓库 URL）
- 本地提交身份暂用占位；拿到 GitHub 信息后可用：
  ```bash
  git commit --amend --reset-author --no-edit
  ```
  再推送
- 提交时不要带上 `kaoyan.db`（已在 `.gitignore`）
- 备份归档建议路径：`D:\code-demo-archive\`（bundle + zip）

推送参考：

```bash
git remote add origin https://github.com/<username>/yanbei-kaoyan-dashboard.git
git push -u origin main --tags
```

---

## 9. 下次开始时的建议顺序

1. 读 `README.md` 和本文件
2. `git status` 确认工作区干净
3. `py app.py` 跑起来，点一遍仪表盘 / 专注 / 历史
4. 和用户确认下一轮优先级；默认建议先做 **专注记录年/月/日下钻**
5. 改动时保持：参数化 SQL、PRG、番茄只追加、手动与番茄分离
6. 改完补一轮 Flask test + 浏览器 E2E，再提交
