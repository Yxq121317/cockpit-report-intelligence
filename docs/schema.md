# 数据模型与接口说明

本文档描述校园数据问答（Text2SQL）项目的严格 8 表结构、示例问题与接口约定。数据库默认为 SQLite `data/p1.db`，也可通过 `P1_DATABASE_URL` 切换 PostgreSQL。

## 1. 表结构

### 1.1 `grades` 年级

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | 整数 | 主键 | 自增 |
| `name` | 字符串 | 唯一、非空 | 如“七年级”“八年级”“九年级” |
| `level` | 整数 | 唯一、非空 | 年级数值，1/2/3 |
| `sort` | 整数 | 非空 | 展示排序 |

### 1.2 `teachers` 教师

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | 整数 | 主键 | 自增 |
| `teacher_no` | 字符串 | 唯一、非空 | 教师工号 |
| `name` | 字符串 | 非空 | 姓名 |
| `gender` | 字符串 | 非空 | 男 / 女 |
| `subject` | 字符串 | 非空 | 任教学科 |
| `title` | 字符串 | 非空 | 职称 |
| `phone` | 字符串 | 可空 | 联系电话 |
| `hire_date` | 日期 | 非空 | 入职日期 |

### 1.3 `classes` 班级

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | 整数 | 主键 | 自增 |
| `grade_id` | 整数 | 外键 → `grades.id` | 所属年级 |
| `name` | 字符串 | 唯一、非空 | 班级名，如“八年级一班” |
| `head_teacher_id` | 整数 | 外键 → `teachers.id`，可空 | 班主任 |
| `classroom` | 字符串 | 可空 | 教室 |

### 1.4 `students` 学生

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | 整数 | 主键 | 自增 |
| `student_no` | 字符串 | 唯一、非空 | 学号 |
| `name` | 字符串 | 非空 | 姓名 |
| `gender` | 字符串 | 非空 | 男 / 女 |
| `birth_date` | 日期 | 非空 | 出生日期 |
| `grade_id` | 整数 | 外键 → `grades.id` | 所属年级 |
| `class_id` | 整数 | 外键 → `classes.id` | 所属班级 |
| `enrollment_date` | 日期 | 非空 | 入学日期 |
| `status` | 字符串 | 非空 | 在读状态 |
| `phone` | 字符串 | 可空 | 联系电话 |
| `address` | 字符串 | 可空 | 家庭住址 |

### 1.5 `parents` 家长

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | 整数 | 主键 | 自增 |
| `student_id` | 整数 | 外键 → `students.id` | 对应学生 |
| `name` | 字符串 | 非空 | 姓名 |
| `relation` | 字符串 | 非空 | 父亲 / 母亲等 |
| `phone` | 字符串 | 可空 | 联系电话 |
| `occupation` | 字符串 | 可空 | 职业 |
| `is_primary` | 布尔 | 非空 | 是否主要联系人 |

### 1.6 `attendance` 出勤

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | 整数 | 主键 | 自增 |
| `student_id` | 整数 | 外键 → `students.id` | 学生 |
| `class_id` | 整数 | 外键 → `classes.id` | 班级 |
| `date` | 日期 | 非空 | 考勤日期 |
| `status` | 字符串 | 非空 | 正常 / 迟到 / 早退 / 缺勤 / 请假 |
| `check_in_time` | 时间 | 可空 | 签到时间 |
| `remark` | 字符串 | 可空 | 备注 |

唯一约束：`(student_id, date)`。

### 1.7 `leave_requests` 请假

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | 整数 | 主键 | 自增 |
| `student_id` | 整数 | 外键 → `students.id` | 学生 |
| `class_id` | 整数 | 外键 → `classes.id` | 班级 |
| `leave_type` | 字符串 | 非空 | 事假 / 病假等 |
| `start_date` | 日期 | 非空 | 开始日期 |
| `end_date` | 日期 | 非空 | 结束日期 |
| `days` | 数值 | 非空 | 请假天数 |
| `reason` | 字符串 | 非空 | 请假原因 |
| `status` | 字符串 | 非空 | 待审批 / 已批准 / 已驳回 |
| `applied_at` | 时间 | 非空 | 申请时间 |
| `approver_teacher_id` | 整数 | 外键 → `teachers.id`，可空 | 审批教师 |
| `approved_at` | 时间 | 可空 | 审批时间 |
| `remark` | 字符串 | 可空 | 备注 |

### 1.8 `timetable` 课程表

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | 整数 | 主键 | 自增 |
| `class_id` | 整数 | 外键 → `classes.id` | 班级 |
| `weekday` | 整数 | 非空 | 1–5，周一至周五 |
| `period` | 整数 | 非空 | 第几节 |
| `subject` | 字符串 | 非空 | 学科 |
| `teacher_id` | 整数 | 外键 → `teachers.id` | 任课教师 |
| `classroom` | 字符串 | 可空 | 教室 |
| `semester` | 字符串 | 非空 | 学期 |
| `start_time` | 时间 | 可空 | 上课时间 |
| `end_time` | 时间 | 可空 | 下课时间 |

唯一约束：`(class_id, weekday, period, semester)`。

## 2. 指标口径

- 应出勤：某范围内每个学生每个考勤日的记录数。
- 实际出勤：状态为“正常”或“迟到”的记录数。
- 出勤率 =（正常 + 迟到）/ 应出勤 × 100%。
- 迟到次数：状态为“迟到”的记录数。
- 待审批请假：`leave_requests.status = '待审批'`。
- 课时：`timetable` 中按教师统计的记录数。

## 3. 示例问题

| 问题 | 预期结果 |
|---|---|
| 上周八年级各班出勤率 | 按班级分组的出勤率 |
| 最近两周迟到次数最多的班级 | 班级迟到次数排行 |
| 本周待审批请假有多少 | 一个计数 |
| 本周请假名单 | 请假明细列表 |
| 八年级二班本周课程表 | 该班周一至周五课程 |
| 李雷家长联系方式 | 家长姓名、关系、电话 |
| 各年级学生人数 | 按年级分组计数 |
| 八年级一班有多少学生 | 班级人数 |
| 八年级一班班主任 | 班主任姓名与学科 |
| 本学期老师课时排名 | 教师课时排行 |
| 张小明最近出勤情况 | 出勤率与状态分布 |
| 全校有多少男生和女生 | 按性别计数 |

无法识别的问题会返回 `need_clarification=true`，并在 `answer` 中给出可用问法。

## 4. 接口

### 4.1 健康检查

```http
GET /api/health
```

### 4.2 查询元数据

```http
GET /api/query/meta
```

返回数据库类型、表数量、最大行数、LLM 模式、8 张表及列说明、示例问题。

### 4.3 自然语言问数

```http
POST /api/query/nl
Content-Type: application/json

{ "question": "上周八年级各班出勤率" }
```

响应：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "question": "上周八年级各班出勤率",
    "title": "上周八年级各班出勤率",
    "sql": "SELECT ... LIMIT 200",
    "explanation": "按班级统计正常与迟到人次，计算出勤率。",
    "columns": [
      { "key": "班级", "label": "班级", "type": "string" },
      { "key": "出勤率", "label": "出勤率", "type": "number" }
    ],
    "rows": [
      { "班级": "八年级一班", "出勤率": 92.0 }
    ],
    "chart": {
      "type": "bar",
      "x": "班级",
      "y": ["出勤率"],
      "series": [
        { "name": "出勤率", "key": "出勤率" }
      ]
    },
    "answer": "上周八年级各班出勤率：八年级一班 92.0%，八年级二班 95.3%。",
    "summary": "共 2 个班级。",
    "row_count": 2,
    "elapsed_ms": 12,
    "need_clarification": false,
    "degraded": false,
    "model_mode": "mock"
  },
  "trace_id": "..."
}
```

### 4.4 导出

```http
POST /api/query/export
Content-Type: application/json

{
  "format": "xlsx",
  "filename": "上周八年级各班出勤率",
  "columns": [
    { "key": "班级", "label": "班级" },
    { "key": "出勤率", "label": "出勤率" }
  ],
  "rows": [
    { "班级": "八年级一班", "出勤率": 92.0 }
  ]
}
```

`format` 为 `xlsx` 或 `csv`。XLSX 带表头样式与冻结首行；CSV 使用 `utf-8-sig`，Excel 打开中文不乱码。

### 4.5 语音转写

```http
POST /api/asr/transcribe
Content-Type: multipart/form-data

audio: voice.wav   # 16 kHz、单声道、PCM16 WAV
language: zh       # 可选
```

后端通过 DashScope `qwen3-asr-flash-realtime` WebSocket 发送裸 PCM，不保存音频文件。响应 `data` 含 `text`、`mode`、`degraded`、`model`、`elapsed_ms`、`error`。未配置 `ASR_API_KEY` 或上游异常时自动降级 mock，并返回固定示例文本。

### 4.6 智能助手

```http
GET /api/assistant/meta
POST /api/assistant/chat
Content-Type: application/json

{ "question": "生成八年级出勤报表" }
```

意图分为 `data_query`、`data_entry`、`report_generation`、`chitchat`。数据查询复用 `/api/query/nl` 的 Text2SQL 与 SQL 安全链路；数据录入与报表生成通过 Function Call 调用内部工具：

| 工具 | 写入表 | 说明 |
|---|---|---|
| `create_student` | `students` | 校验学号唯一、性别、班级 |
| `create_parent` | `parents` | 校验学生存在、11 位手机号、同学生同电话不重复 |
| `record_attendance` | `attendance` | 同一学生同一天 upsert |
| `create_leave_request` | `leave_requests` | 校验日期，自动计算天数，状态待审批 |
| `create_timetable_entry` | `timetable` | 校验班级 / 教师 / 星期 / 节次 / 学期唯一 |
| `generate_report` | 不写表 | 生成 `data/reports/{report_id}.xlsx` 与 sidecar JSON |

多个工具在同一事务中执行，任一失败全部回滚。接口响应不返回服务器绝对路径，只返回 `/api/reports/{report_id}/download`。

### 4.7 报表下载

```http
GET /api/reports/{report_id}/download
```

仅接受 32 位十六进制 `report_id`，返回 XLSX，`Content-Disposition` 同时提供 ASCII 与 UTF-8 文件名。报表类型：

- `attendance_summary`：班级出勤统计，出勤率 =（正常 + 迟到）/ 应出勤
- `leave_summary`：请假明细
- `student_roster`：学生花名册
- `timetable`：课程表
- `grade_overview`：年级概况
- `campus_overview`：校园数据总览模板报表，8 张业务表各一个 Sheet（年级概况、教师名录、班级概况、学生花名册、家长联系表、出勤汇总、请假记录、课程表）

`campus_overview` 的文件仍是单个 XLSX，sidecar JSON 的 `sheets` 字段记录各 Sheet 名称与行数；`row_count` 为所有 Sheet 行数之和。也可以用 `python scripts/generate_campus_report.py --json` 直接生成。

报表只落文件，不新增数据库表，因此数据库仍严格保持 8 张业务表。

### 4.8 飞书群机器人

对接方式为飞书开放平台自建应用的长连接 / 事件回调与消息 API，不是独立 MCP Server。默认由 `lark-oapi` 长连接接收 `im.message.receive_v1`，无需公网回调地址。相关接口：

```http
GET  /api/feishu/meta
POST /api/feishu/events
POST /api/feishu/push/report
```

`GET /api/feishu/meta` 返回：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "mode": "mock",
    "degraded": true,
    "configured": false,
    "default_chat_configured": false,
    "writes_allowed": false,
    "long_connection": "disabled",
    "long_connection_connected": false
  }
}
```

`long_connection` 取值：`disabled`（mock 模式或 `FEISHU_LONG_CONNECTION=false`）、`starting`、`running`、`error`、`stopped`；`long_connection_connected` 表示 WebSocket 当前是否已建立。不返回 `FEISHU_APP_SECRET`、`FEISHU_VERIFICATION_TOKEN`、`FEISHU_ENCRYPT_KEY` 或任何片段。

长连接（默认）：

- 应用启动时，若 `FEISHU_MODE` 为 real 且 `FEISHU_LONG_CONNECTION=true`，后端在守护线程中启动 `lark-oapi` WebSocket 客户端。
- 飞书开放平台的事件订阅需选择「使用长连接接收事件」并订阅 `im.message.receive_v1`，无需请求网址。
- 事件回调在 SDK 线程中只做解析与去重，实际问答交给独立工作线程，避免阻塞心跳与后续事件。
- 发送方为应用自身的消息直接忽略，防止机器人回复自身形成循环。
- 长连接与 HTTP 回调共用同一个 `FeishuService`、同一套 `message_id` 去重表和消息处理链路。
- 断线重连由 `lark-oapi` 负责；关闭后端时尽力断开连接，守护线程随进程退出。

HTTP 事件回调（可选，设置 `FEISHU_LONG_CONNECTION=false` 后使用）：

- 请求头使用 `X-Lark-Request-Timestamp`、`X-Lark-Request-Nonce`、`X-Lark-Signature`。
- 签名算法：`sha256(timestamp + nonce + encrypt_key + raw_body)`，比较使用常量时间比较。
- 配置 `FEISHU_ENCRYPT_KEY` 时，事件体为 `{"encrypt": "..."}`；使用 AES-256-CBC 解密，密钥为 `sha256(encrypt_key)`，IV 为密文前 16 字节，PKCS7 去填充。
- 解密后再校验 `header.token`（若配置了 `FEISHU_VERIFICATION_TOKEN`）。
- URL 校验请求原样回显 `challenge`。
- 订阅事件 `im.message.receive_v1`；按 `message_id` 去重，同一消息只处理一次。
- 群消息会调用与网页端相同的意图识别与 Text2SQL。查询结果以互动卡片回复；报表请求额外上传并发送 XLSX 文件。
- 机器人默认只读：`data_entry` 意图直接回复拒绝提示，不执行写工具。`FEISHU_ALLOW_WRITES=true` 才会在同一事务边界内执行写工具。
- 不新增群消息、会话、推送记录或审计表；mock 模式发送记录仅保存在进程内存。

主动推送报表：

```http
POST /api/feishu/push/report
Content-Type: application/json

{ "report_id": "32 位十六进制", "chat_id": "oc_xxx（可选）" }
```

先发送报表摘要卡片，再发送 `data/reports/{report_id}.xlsx`。`chat_id` 缺省时依次使用服务端 `FEISHU_DEFAULT_CHAT_ID`、mock 模式的 `FEISHU_MOCK_CHAT_ID`。返回字段包括 `report_id`、`title`、`filename`、`row_count`、`chat_id`、`mode`、`degraded`。

模式为 `auto | real | mock`。未配置应用凭据或 `FEISHU_MODE=mock` 时完全离线，不启动长连接、不发起网络请求；真实模式异常时按调用点返回业务错误，错误信息经过脱敏。`FEISHU_LONG_CONNECTION` 默认 `true`，仅在真实模式下生效。

## 5. SQL 安全规则

1. 只允许单条 `SELECT`。
2. 只允许访问 8 张表及其列，禁止 `SELECT *`。
3. 拒绝注释、多语句、写操作、DDL、`PRAGMA`、`ATTACH`。
4. 自动补 `LIMIT 200`，已有更小限制时保留。
5. SQLite 使用只读连接与 3 秒进度超时；PostgreSQL 使用只读事务与 `statement_timeout=3000`。
6. 真实模型生成的 SQL 校验或执行失败时重试一次，仍失败则回退内置模板并标记降级。
