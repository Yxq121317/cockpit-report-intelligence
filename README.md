# 校园数据问答（Text2SQL）

用一句中文问题或一段语音操作校园数据：语音 → 文本 → 意图识别 → Text2SQL 或 Function Call 内部工具 → 结果表 + 图表 + 中文结论 / 数据录入 / 可下载报表。

后端 FastAPI + SQLAlchemy，默认 SQLite，可通过 `P1_DATABASE_URL` 切换 PostgreSQL；前端 Vue 3 + TypeScript + Element Plus + ECharts。无登录，打开页面即可提问；`LLM_MODE=mock` 与 `ASR_MODE=mock` 时完全离线且结果确定。

## 功能

- **自然语言问数**：支持出勤、迟到、请假、课程表、学生/教师/班级/家长等常见问题。
- **中文回答**：除结果表与图表外，同时返回中文结论、口径说明与标题。
- **SQL 展示与复制**：界面展示本次执行的 SQL，便于核对。
- **结果导出**：查询结果可导出 XLSX（带表头样式、冻结首行）或 UTF-8 BOM 的 CSV，中文不乱码。
- **只读安全**：sqlglot 解析，仅允许单条 `SELECT`，8 表 / 列白名单，禁止注释与写操作，自动 `LIMIT 200`，3 秒超时。
- **双数据库**：SQL 以 SQLite 语法生成，执行前转译为当前方言；SQLite 与 PostgreSQL 均启用只读事务与超时。
- **语音转写**：浏览器录制 16 kHz 单声道 WAV，后端通过 DashScope `qwen3-asr-flash-realtime` WebSocket 流式转写；音频不落盘。
- **意图识别**：把问题分为数据查询、数据录入、报表生成、闲聊四类；真实模型返回 JSON，失败时回退关键词规则。
- **Function Call 工具**：新增学生 / 家长、录入考勤、提交请假、录入课程表、生成报表，共 6 个内部工具。
- **报表下载**：出勤、请假、花名册、课程表、年级概况、校园数据总览 6 类 XLSX 报表，文件写入 `data/reports/`，不新增数据库表。校园数据总览按 8 张业务表各生成一个 Sheet，可作为模板报表重复生成。
- **飞书群机器人**：群内 @机器人 提问，复用同一套意图识别与 Text2SQL；生成的报表可一键推送到飞书群，文件随卡片一起发送。默认通过飞书长连接接收事件，无需公网回调地址；默认只读，群内数据录入不会执行。
- **可离线**：`LLM_MODE=mock` 与 `ASR_MODE=mock` 使用内置规则，不依赖任何外部服务；真实模式失败自动降级并标记 `degraded`。飞书未配置应用凭据时同样自动 mock，不发起任何网络请求。
- **LLM 监控与评测**：通过 Langfuse 追踪每次问数/助手请求与模型调用，持续统计任务完成率、工具调用准确率、忠实度与表结构召回率；线上失败案例可按类别分析，用于迭代 Prompt 与 schema 检索策略。顶栏“评测看板”可在应用内查看实时指标、黄金评测与最近失败，并一键重新运行黄金评测。Langfuse 默认关闭，mock 模式完全离线。

## 数据模型：严格 8 张表

| 表 | 说明 | 关键字段 |
|---|---|---|
| `grades` | 年级 | `name`、`level`、`sort` |
| `teachers` | 教师 | `teacher_no`、`name`、`gender`、`subject`、`title`、`phone`、`hire_date` |
| `classes` | 班级 | `grade_id`、`name`、`head_teacher_id`、`classroom` |
| `students` | 学生 | `student_no`、`name`、`gender`、`birth_date`、`grade_id`、`class_id`、`enrollment_date`、`status`、`phone`、`address` |
| `parents` | 家长 | `student_id`、`name`、`relation`、`phone`、`occupation`、`is_primary` |
| `attendance` | 出勤 | `student_id`、`class_id`、`date`、`status`（正常/迟到/早退/缺勤/请假）、`check_in_time`、`remark` |
| `leave_requests` | 请假 | `student_id`、`class_id`、`leave_type`、`start_date`、`end_date`、`days`、`reason`、`status`、`applied_at`、`approver_teacher_id`、`approved_at`、`remark` |
| `timetable` | 课程表 | `class_id`、`weekday`、`period`、`subject`、`teacher_id`、`classroom`、`semester`、`start_time`、`end_time` |

种子数据使用固定随机种子 `20261103` 与固定学期（`2026-09-01` 至 `2026-11-03`），生成 3 个年级、24 名教师、6 个班级、180 名学生、360 条家长记录、8,280 条考勤、60 条请假、180 条课程表记录。张小明固定在八年级一班，李雷固定在八年级二班。

## 快速开始

要求：Windows PowerShell、Python 3.12+、Node.js 20+。

```powershell
cd D:\Project\smartdeck_prepgenius
.\scripts\setup.ps1      # 创建 .venv，安装后端与前端依赖
.\scripts\seed.ps1       # 初始化 8 表与示例数据
.\scripts\dev.ps1        # 后端 8001 + 前端 5171，默认 mock
```

打开 <http://localhost:5171>，无需登录。

手动启动：

```powershell
# 后端
cd backend
python -m app.seed.seed --reset
$env:LLM_MODE = "mock"
python -m uvicorn app.main:app --reload --port 8001

# 前端（另开一个终端）
cd frontend
npm install
npm run dev
```

- `.\scripts\seed.ps1 -Reset`：删表重建并重新生成示例数据。
- `.\scripts\dev.ps1 -Mode auto`：有 Key 时走真实模型，否则自动 mock。
- `.\scripts\dev.ps1 -Mode real`：强制真实模型，失败自动降级到模板。

## 切换 PostgreSQL

默认使用 `data/p1.db`。在本地未被 Git 跟踪的 `.env` 中填写完整连接串即可切换：

```dotenv
P1_DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DBNAME
```

留空或删除该行则回到 SQLite。切换后任选一种初始化方式：

```powershell
# 方式一：在目标库中重建 8 表并生成示例数据（会删除目标库全部旧表）
.\scripts\seed.ps1 -Reset

# 方式二：把现有 SQLite 数据原样迁移到 PostgreSQL
python scripts/migrate_sqlite_to_postgres.py
```

迁移脚本默认读取 `P1_DATABASE_URL`；目标库非空时默认拒绝，确认覆盖可加 `--truncate`。脚本只打印表名与行数，不打印连接串中的密码。

## 示例问题

- 上周八年级各班出勤率
- 最近两周迟到次数最多的班级
- 本周待审批请假有多少
- 本周请假名单
- 八年级二班本周课程表
- 李雷家长联系方式
- 各年级学生人数
- 八年级一班有多少学生
- 八年级一班班主任
- 本学期老师课时排名
- 张小明最近出勤情况
- 全校有多少男生和女生
- 生成八年级出勤报表
- 生成校园数据总览模板报表
- 新增学生 测试同学，学号 TEST2026001，性别男，班级八年级一班
- 给李雷登记今天的正常考勤
- 给李雷提交明天的病假申请

语音输入：点击“语音输入”开始录音，再点一次结束；转写文本会填回输入框，确认无误后点“开始处理”。数据录入属于写操作，不会在转写后自动提交。请使用 `http://localhost:5171` 或 HTTPS 访问；若提示权限被拒绝，在浏览器地址栏左侧把本站麦克风权限设为“允许”，并确认 Windows“隐私和安全性 → 麦克风”已允许桌面应用访问。

## API

统一响应：`{code, message, data, trace_id}`；`code=0` 表示成功。

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/health` | 健康检查 |
| `GET` | `/api/query/meta` | 数据库类型、8 表结构、示例问题、LLM 模式 |
| `POST` | `/api/query/nl` | 自然语言问数，请求体 `{ "question": "..." }` |
| `POST` | `/api/query/export` | 导出 XLSX/CSV，请求体含 `format`、`filename`、`columns`、`rows` |
| `GET` | `/api/assistant/meta` | 意图、6 个工具、示例指令与 ASR 状态 |
| `POST` | `/api/assistant/chat` | 意图识别 + Text2SQL / Function Call，请求体 `{ "question": "..." }` |
| `POST` | `/api/asr/transcribe` | multipart 上传 `audio`（WAV PCM16）与可选 `language` |
| `GET` | `/api/reports/{report_id}/download` | 下载已生成的 XLSX 报表 |
| `GET` | `/api/feishu/meta` | 飞书模式、长连接状态、是否降级、是否配置、是否允许写入（不返回任何凭据） |
| `POST` | `/api/feishu/events` | HTTP 事件回调：URL 校验、`im.message.receive_v1` 群消息（默认长连接无需此接口） |
| `POST` | `/api/feishu/push/report` | 主动推送报表到飞书群，请求体 `{ "report_id": "...", "chat_id": "..." }` |
| `GET` | `/api/eval/summary` | 任务完成率、工具调用准确率、忠实度、失败分类与 Langfuse 状态 |
| `GET` | `/api/eval/failures?limit=50` | 最近失败案例（内存最近 200 条） |
| `GET` | `/api/eval/golden` | 最近一次黄金评测结果 |
| `POST` | `/api/eval/golden/run?push=false` | 重新运行黄金评测；`push=true` 才上报 Langfuse |

`/api/query/nl` 返回的 `data` 主要字段：

- `question`、`title`、`answer`、`summary`、`explanation`
- `sql`、`columns`、`rows`、`row_count`、`elapsed_ms`
- `chart`：`{ type, x, y, series }`；`type` 为 `bar` / `line` / `table`，`x` 为横轴列，`y` 为数值列，`series` 为图例映射
- `need_clarification`：问题无法识别时为 `true`，此时 `answer` 给出示例问法
- `degraded`、`model_mode`：是否降级、本次使用真实模型还是内置模板

`POST /api/assistant/chat` 请求体为 `{ "question": "...", "force_report": false }`。`force_report=true` 时跳过意图分类，直接按问题生成对应报表；网页端“按问题生成报表”按钮使用该参数。

`/api/assistant/chat` 返回的 `data` 主要字段：

- `intent`、`intent_label`、`intent_confidence`、`intent_reason`：意图分类结果
- `reply`：面向用户的中文回复
- `query`：意图为数据查询时，与 `/api/query/nl` 相同的查询结果；其他意图为 `null`
- `tool_calls`：已执行的内部工具列表，含 `name`、`label`、`arguments`、`status`、`summary`
- `report`：生成报表时的 `report_id`、`title`、`filename`、`row_count`、`download_url`
- `degraded`、`model_mode`、`elapsed_ms`

内部工具：

| 工具 | 说明 |
|---|---|
| `create_student` | 新增学生，校验学号唯一、性别、班级 |
| `create_parent` | 为已有学生新增家长，校验手机号与重复联系人 |
| `record_attendance` | 录入或更新考勤，同一学生同一天唯一 |
| `create_leave_request` | 提交待审批请假，自动计算天数 |
| `create_timetable_entry` | 录入课程，校验班级 / 星期 / 节次 / 学期唯一 |
| `generate_report` | 生成 XLSX 报表文件，不写数据库 |

报表类型：`attendance_summary`、`leave_summary`、`student_roster`、`timetable`、`grade_overview`、`campus_overview`。文件只存放在 `data/reports/`，元数据写同目录 sidecar JSON，不新增第 9 张表。

`campus_overview`（校园数据总览）是模板报表：年级概况、教师名录、班级概况、学生花名册、家长联系表、出勤汇总、请假记录、课程表各一个 Sheet，覆盖全部 8 张业务表。出勤与请假按日期区间统计，其余 Sheet 按班级 / 年级范围过滤，教师名录始终保持全校口径。

网页端输入区提供 **“按问题生成报表”** 按钮：读取输入框中的当前问题并生成对应报表，例如“八年级各班出勤率”会生成八年级出勤报表，“本周请假情况”会生成本周请假报表；输入框为空时生成校园数据总览模板报表。

除网页问数外，也可直接运行脚本生成，或在 Claude Code 中通过项目技能 `.claude/skills/campus-report/SKILL.md` 调用：

```powershell
python scripts/generate_campus_report.py --json
python scripts/generate_campus_report.py --target 八年级 --start-date 2026-09-01 --end-date 2026-11-03 --json
```

脚本输出 `report_id`、`download_url` 与各 Sheet 行数；`--push` 会推送到飞书群，属于外部可见操作，仅在明确需要时使用。

## LLM 监控与效果评测

每次问数与助手请求都会生成 Langfuse trace（LLM generation、工具 span、query/assistant 分数），同时在进程内累计：

- **任务完成率**：问数成功执行，或录入/报表工具全部成功。
- **工具调用准确率**：Function Call 执行成功的工具占比。
- **忠实度**：回答中的数字与日期能从本次 SQL 结果或工具返回数据溯源的比例。
- **表结构召回率**：黄金用例期望表被 schema 检索命中的比例。

real 模式生成 SQL 前会先做表结构检索，只注入相关表和一跳 JOIN 邻居；校验或执行失败后重试时回退完整 8 表。

前端顶栏的“评测看板”抽屉直接读取 `/api/eval/*`，展示线上指标、失败分类、最近失败案例与黄金评测明细，并可在应用内重新运行 14 个黄金用例（写操作回滚、不上报 Langfuse）。需要长期趋势与 trace 时，开启 Langfuse 后到其控制台查看。默认关闭 Langfuse，开启方式与完整指标定义见 [docs/evaluation.md](docs/evaluation.md)。

```powershell
# 离线黄金评测（14 个用例，不写数据库、不提交写操作）
python scripts/evaluate_llm.py
python scripts/evaluate_llm.py --mode real --json

# 分析线上失败案例（需先设置 EVAL_FAILURE_LOG=true）
python scripts/analyze_failures.py
```

`--push` 与 `LANGFUSE_ENABLED=true` 才会上报 Langfuse，属于外部可见操作；mock 模式不会发起任何网络请求。

## 飞书群机器人

对接方式为**飞书开放平台自建应用 + 长连接 / 事件回调 + 消息 API**，没有引入独立 MCP Server。后端默认使用 `lark-oapi` 长连接接收群消息事件，无需公网地址；同时保留 `POST /api/feishu/events` 供 HTTP 回调模式使用。两种使用形态：

- **群内问答**：在群里 @机器人 提问，后端复用同一套意图识别与 Text2SQL；查询结果以飞书互动卡片回复，包含中文结论、结果表前几行与图表/网页提示。
- **报表推送**：网页端生成报表后点击“推送到飞书”，或调用 `POST /api/feishu/push/report`；先发结果卡片，再发送 XLSX 文件。

安全与边界：

1. 长连接由 `lark-oapi` 使用应用凭据建立，无需暴露公网地址；事件回调模式则校验 `X-Lark-Signature`（`sha256(timestamp + nonce + encrypt_key + body)`），配置了 `FEISHU_ENCRYPT_KEY` 时按 AES-256-CBC 解密事件，再校验 verification token。回调签名或解密失败返回 403。
2. 事件按 `message_id` 去重；同一消息只处理一次。长连接和回调同时开启时也不会重复处理。
3. 群机器人**默认只读**：识别到数据录入意图时回复提示并拒绝执行。确需开启时显式设置 `FEISHU_ALLOW_WRITES=true`，写入仍只操作既有 8 张表。
4. 飞书机器人不新建任何数据表，不保存群消息、会话或推送记录；mock 模式下的发送记录只存在于进程内存。
5. `/api/feishu/meta` 只返回模式、长连接状态与配置状态，不返回 `FEISHU_APP_SECRET`、verification token、encrypt key 或任何片段；日志与错误信息同样脱敏。

离线演示：未配置 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 且 `FEISHU_MODE=auto` 时自动 mock，不发起网络请求，推送目标为内存中的 `oc_mock_campus`。`pytest` 与本地演示均使用该模式。

接入真实飞书：

1. 在飞书开放平台创建企业自建应用，开启机器人能力，申请接收群消息、以应用身份发消息、上传与发送文件所需权限。
2. 把机器人加入目标群。可用配置脚本安全写入凭据并检查连通性（密钥不回显）：

   ```powershell
   python scripts/feishu_setup.py --chat-id oc_xxx
   ```

   脚本会把 `FEISHU_MODE=real`、`FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_DEFAULT_CHAT_ID` 等写入本地未被 Git 跟踪的 `.env`，然后校验凭据并列出机器人所在群。验证成功后可用 `--send-test` 向默认群发送一条测试文本。
3. 在开放平台「事件与回调 → 事件配置」中选择**使用长连接接收事件**，订阅 `im.message.receive_v1`。无需公网地址，也不需要配置请求网址。
4. 重启后端。日志出现 `飞书长连接 | 已启动` 后，`GET /api/feishu/meta` 会显示 `mode=real`、`configured=true`、`long_connection=running` 与 `long_connection_connected=true`。`FEISHU_DEFAULT_CHAT_ID` 用于不传 `chat_id` 的主动推送。
5. 如需改用 HTTP 回调，设置 `FEISHU_LONG_CONNECTION=false`，再把事件订阅地址设为 `https://<公网 HTTPS 域名>/api/feishu/events`，按回调模式完成 URL 校验。

## 安全与 SQL 约束

1. 仅允许单条 `SELECT`，拒绝 `INSERT/UPDATE/DELETE/DDL/PRAGMA/ATTACH`、多语句和注释。
2. 只允许访问上述 8 张表及其列，禁止 `SELECT *`。
3. 自动补充 `LIMIT 200`；已有更小 `LIMIT` 时保留。
4. SQLite 使用 `PRAGMA query_only=ON` + progress handler；PostgreSQL 使用 `SET TRANSACTION READ ONLY` + `statement_timeout=3000`。
5. 真实模型生成的 SQL 校验或执行失败时带错误重试一次；仍失败则回退到内置模板并标记降级。
6. Function Call 写工具只操作既有 8 张表，校验必填字段与业务唯一性；多个工具在同一事务中执行，任一失败全部回滚。
7. 报表只生成 `data/reports/` 下的文件，不创建报告表、审计表或查询历史表。
8. 语音接口不落盘音频；未配置 ASR Key 或上游异常时自动 mock，不阻断页面。
9. 飞书事件回调必须通过签名（及可选的 AES 解密、token）校验；群机器人默认只读，不新增消息、会话、推送记录表。

## 目录结构

```text
backend/app/
  api/        HTTP 接口（health、query、assistant、asr、reports、feishu、eval）
  core/       配置、数据库、错误、LLM 网关、SQL 安全校验、Prompt 加载、Langfuse 观测、Schema 检索
  eval/       黄金用例与评测运行器
  models/     严格 8 张表的 SQLAlchemy 模型
  schemas/    请求 / 响应模型
  services/   Text2SQL、意图/工具编排、ASR、飞书、报表、XLSX/CSV 导出与效果评测
  seed/       确定性示例数据
  prompts/    Text2SQL、意图分类、工具规划提示词（保留历史版本）
frontend/src/
  api/        REST 封装（query、assistant、evaluation，含飞书报表推送）
  components/ BaseChart.vue（ECharts 生命周期与自适应）、EvalDashboard.vue（评测看板）
  utils/      WAV 录音与重采样、格式化
  views/query/ 单页问答界面（语音、意图、工具、报表、评测看板入口）
data/         默认 SQLite 数据库、reports/ 报表与 evals/ 评测结果（gitignore）
scripts/      setup / seed / dev / 迁移 / 评测 / 失败案例分析
docs/         数据模型、接口说明与评测文档（evaluation.md）
```

## 验证

```powershell
# 仓库根目录
python -m pytest backend/tests -q

# 前端类型检查与构建
cd frontend
npm run build
```

当前基线：后端 122 项测试通过，前端 `npm run build` 通过。

## 配置与安全

- 配置源为根目录 `config.py`，读取同目录 `.env`；所有字段都有默认值，缺少 `.env` 也能启动。
- 可配置项：`P1_DATABASE_URL`、`API_KEY`、`MODEL_NAME`、`BASE_URL`、`LLM_MODE`，以及 `ASR_MODE`、`ASR_API_KEY`、`ASR_BASE_URL`、`ASR_MODEL_NAME`、`ASR_LANGUAGE`、`ASR_SAMPLE_RATE`、`ASR_AUDIO_FORMAT`、`ASR_TIMEOUT_SECONDS`、`ASR_MOCK_TEXT`。
- 飞书配置：`FEISHU_MODE`、`FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_VERIFICATION_TOKEN`、`FEISHU_ENCRYPT_KEY`、`FEISHU_API_BASE`、`FEISHU_DEFAULT_CHAT_ID`、`FEISHU_MOCK_CHAT_ID`、`FEISHU_ALLOW_WRITES`、`FEISHU_LONG_CONNECTION`、`FEISHU_TIMEOUT_SECONDS`。
- Langfuse 配置：`LANGFUSE_ENABLED`、`LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY`、`LANGFUSE_HOST`、`LANGFUSE_ENVIRONMENT`、`LANGFUSE_RELEASE`、`LANGFUSE_TIMEOUT_SECONDS`、`LANGFUSE_FLUSH_INTERVAL_SECONDS`、`LANGFUSE_CAPTURE_CONTENT`；默认关闭，关闭或缺少凭据时不发起网络请求。
- 评测配置：`EVAL_FAILURE_LOG`、`EVAL_FAILURE_LOG_PATH`、`EVAL_GOLDEN_OUTPUT`、`TEXT2SQL_SCHEMA_RETRIEVAL`；只写内存或本地文件，不新增数据库表。
- `LLM_MODE=auto` 且无 Key 时自动 mock；`real` 上游异常时降级 mock 并保留错误标记。
- `ASR_MODE=auto` 且有 Key 与 Base URL 时走 DashScope，否则 mock；真实转写异常时降级 mock 并保留脱敏错误。
- `FEISHU_MODE=auto` 且缺少应用凭据时自动 mock；`real` 缺凭据同样降级 mock，推送记录只留在内存，不发起网络请求。
- **`.env` 含真实密钥，只保留在本地且不得提交**；仓库只提交 `.env.example`（占位符）。任何日志、文档、接口响应都不得打印密钥或数据库密码。
- `FEISHU_APP_SECRET`、`FEISHU_VERIFICATION_TOKEN`、`FEISHU_ENCRYPT_KEY` 只写在本地 `.env`，`.env.example` 中保持为空占位符。
- 若密钥曾进入 Git 提交历史，推送前应轮换。

更多细节见 [docs/schema.md](docs/schema.md) 与 [CLAUDE.md](CLAUDE.md)。
