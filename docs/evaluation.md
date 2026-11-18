# LLM 监控与效果评测

本文说明本项目的 LLM 观测、效果指标、失败案例分析，以及基于失败样本优化
Prompt 与表结构检索（schema retrieval）的闭环。所有评测只使用现有 8 张业务
表，不新增数据库表；Langfuse 默认关闭，mock / 离线模式不发起任何网络请求。

## 1. 指标定义

| 指标 | 键 | 计算方式 |
|---|---|---|
| 任务完成率 | `task_completion` | 问数成功执行且不需要澄清；录入/报表的全部工具调用成功。澄清、SQL 校验失败、工具报错计为未完成。被归为 `chitchat` 但既不是问候也不是帮助请求的输入，同样按澄清失败计为未完成。 |
| 工具调用准确率 | `tool_call_accuracy` | Function Call 中执行成功的工具数 / 本次规划的工具数；没有规划出工具时按 1 次失败调用计算。闲聊与纯查询不计入。 |
| 忠实度 | `faithfulness` | 回答中的数字与日期能在本次 SQL 结果、工具返回数据或 `row_count` 中找到依据的比例；没有数字时视为 1.0。 |
| 表结构召回率 | `schema_recall` | 黄金用例期望表被 schema 检索命中的比例，用于评估检索策略；只在黄金评测中计算。 |

指标定义同时在 `backend/app/services/evaluation.py` 的 `METRIC_DEFINITIONS`
中维护，并通过 `GET /api/eval/summary` 返回。

写入 Langfuse 的分数名：问数 trace 使用 `query_task_completion` 与
`query_faithfulness`；助手 trace 使用 `task_completion`、`tool_call_accuracy`
与 `faithfulness`；黄金评测额外写入 `intent_accuracy`、`case_passed`、
`pass_rate` 与 `schema_recall`。

忠实度采用确定性的数字/日期溯源，不使用 LLM-as-judge：同一结果可复现、
零额外模型成本，也符合 mock 模式的离线确定性要求。`row_count` 与工具返回
的学号、报表 ID 等标识符会作为额外依据参与比对。

## 2. 观测数据流

```text
HTTP / 飞书消息
  └─ observability.trace("assistant.chat" 或 "text2sql.answer")
       ├─ observability.generation("llm.text2sql" / "llm.intent" / "llm.tool_plan")
       ├─ observability.span("assistant.tools")
       └─ evaluation.record_query / record_assistant
            ├─ 进程内指标与失败环形队列（最多 200 条）
            ├─ 可选本地 JSONL（EVAL_FAILURE_LOG=true）
            └─ Langfuse score-create（LANGFUSE_ENABLED=true）
```

- `ASSISTANT` 的查询分支复用 `text2sql.answer_question(..., record=False)`，
  由 `assistant.chat` 统一记录一次，避免同一次请求重复计数。
- 黄金评测传入 `record=False`，不污染线上指标统计。
- 进程内指标随服务重启清零；需要长期趋势时以 Langfuse 中持久化的分数为准。
- Langfuse 事件通过公开 ingestion API（`POST /api/public/ingestion`，
  HTTP Basic 认证）批量上报，不依赖 SDK 版本；事件先进入内存队列，由后台
  线程按 `LANGFUSE_FLUSH_INTERVAL_SECONDS` 刷新。
- 未启用或缺少凭据时客户端所有方法为空操作，状态只输出原因、主机与环境，
  不输出公钥、私钥或请求内容。

## 3. 配置

所有配置写入根目录本地 `.env`（不提交 Git），`.env.example` 只放占位符。

```dotenv
# Langfuse（默认关闭；关闭时不发起网络请求）
LANGFUSE_ENABLED=false
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=https://cloud.langfuse.com
LANGFUSE_ENVIRONMENT=production
LANGFUSE_RELEASE=smartdeck-0.2.0
LANGFUSE_TIMEOUT_SECONDS=5
LANGFUSE_FLUSH_INTERVAL_SECONDS=1
LANGFUSE_CAPTURE_CONTENT=true

# 效果评测
EVAL_FAILURE_LOG=false
EVAL_FAILURE_LOG_PATH=data/evals/failure_cases.jsonl
EVAL_GOLDEN_OUTPUT=data/evals/latest.json
TEXT2SQL_SCHEMA_RETRIEVAL=true
```

- `LANGFUSE_CAPTURE_CONTENT=false` 时不上报 prompt 与生成内容，只保留 token、
  延迟、模型与分数等元数据。
- `TEXT2SQL_SCHEMA_RETRIEVAL=false` 时 real 模式始终向模型提供完整 8 表结构，
  便于对比检索策略效果。
- 启动日志会输出 `观测状态 | Langfuse: 开启/关闭（原因）`。
- 自建 Langfuse v4 时，需要在 `langfuse-web` 与 `langfuse-worker` 同时设置
  `LANGFUSE_MIGRATION_V4_WRITE_MODE=dual`：v4 默认 `events_only`，旧版
  ingestion 接口只接收 score，trace / span / generation 会返回 207 并被丢弃。
  本项目客户端仍使用经典 ingestion 协议，因此必须保持 `dual`。

## 4. 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/eval/summary` | 线上累计指标、失败分类、Langfuse 状态与指标定义 |
| GET | `/api/eval/failures?limit=50` | 最近失败案例，最多 200 条，最新在前 |
| GET | `/api/eval/golden` | 最近一次黄金评测结果；文件不存在时 `available=false` |
| POST | `/api/eval/golden/run?push=false` | 重新运行黄金用例；`push=true` 才上报 Langfuse |

响应仍为统一结构 `{code, message, data, trace_id}`。失败案例包含问题、意图、
SQL、回答摘要、失败类别与三项分数，不包含密钥或数据库连接串。

## 5. 黄金评测

黄金用例定义在 `backend/app/eval/golden.py`，覆盖：

- 查询：出勤率、迟到排行、待审批请假、课程表、家长联系方式、人数统计、
  班主任、教师课时。
- 录入：登记考勤、新增学生、提交病假申请。
- 报表：出勤报表、请假报表。
- 闲聊：帮助类问题。

评测在独立数据库会话中执行；写工具统一 `commit=False` 后回滚，生成的报表
文件随后删除。默认使用 mock 网关，完全离线且结果确定。

```powershell
# 仓库根目录
python scripts/evaluate_llm.py                 # mock，写入 data/evals/latest.json
python scripts/evaluate_llm.py --json          # 完整 JSON
python scripts/evaluate_llm.py --mode real     # 使用 .env 中的真实模型
python scripts/evaluate_llm.py --no-write      # 不落盘
python scripts/evaluate_llm.py --push          # 额外上报 Langfuse（外部操作）
```

当前 mock 基线：14 个用例全部通过，意图准确率、任务完成率、工具调用准确率、
忠实度、表结构召回率均为 100%。用例不通过时脚本返回退出码 1，可直接用于
CI 或优化前后的对比。

## 6. 失败案例与优化闭环

开启 `EVAL_FAILURE_LOG=true` 后，线上问数与助手请求的失败样本会追加到
`data/evals/failure_cases.jsonl`（只写本地文件）。失败类别：

| 类别 | 含义 | 优先优化方向 |
|---|---|---|
| `clarification` | 没有匹配到查询模板或需要澄清；包括被归为闲聊但未识别出数据意图的无效问题 | 补充 mock 模板与提示词示例，区分问候/帮助与无效输入 |
| `query_failed` | SQL 校验或执行失败 | 检查表/字段、失败重试时回退完整表结构 |
| `unfaithful_answer` | 回答含无法溯源的数字或日期 | 收紧回答模板，只引用 SQL 结果 |
| `tool_call_failed` | 工具缺失、参数错误或执行失败 | 补充意图关键词与参数校验 |
| `assistant_failed` | 意图路由后未完成任务 | 检查意图分类与工具规划回退 |

```powershell
python scripts/analyze_failures.py
python scripts/analyze_failures.py --category unfaithful_answer --limit 30
python scripts/analyze_failures.py --json
```

分析脚本按类别、来源与高频问题汇总，并给出对应优化建议。推荐闭环：

1. 用 `analyze_failures.py` 找出高频失败类别与问法。
2. 在 `app/prompts/{task}/v{n+1}.md` 新增提示词版本（保留历史版本），并在
   代码中切换版本常量。
3. 若属于表结构检索遗漏，补充 `core/schema_retrieval.py` 的关键词或 JOIN
   邻居，并新增黄金用例。
4. 运行 `python -m pytest backend/tests -q` 与
   `python scripts/evaluate_llm.py`，确认指标不下降。
5. 需要长期趋势时开启 Langfuse，把同一批分数写入 trace 并在控制台观察。

### 本次优化记录

- **失败样本**：“帮李雷提交明天到后天的病假申请”原关键词规则只匹配
  “请假”，没有识别“病假”，被归为 `chitchat`，工具调用准确率为 0。
- **修复**：`assistant.py` 增加 `_LEAVE_MARKERS`（请假 / 病假 / 事假），
  同步补充录入对象与 mock 工具规划；`intent/v2.md` 明确“病假 / 事假 +
  提交 / 申请”属于 `data_entry`，并区分“待审批请假有多少”属于查询；
  `tool_plan/v2.md` 约束请假类型与报表枚举。该问法已加入黄金用例
  `entry_leave`。
- **检索策略**：real 模式先做 schema linking，只注入相关表与一跳 JOIN
  邻居；失败重试回退完整 8 表。黄金用例的表结构召回率当前为 100%。
- **Prompt v2**：补充业务日期 `as_of`、表结构检索结果、请假与考勤口径、
  失败重试约束，并将“不得编造统计数字”写入提示词；v1 保留不覆盖。

## 7. 表结构检索策略

`backend/app/core/schema_retrieval.py` 在 real 模式生成 SQL 前做 schema
linking：

1. 按问题中的业务关键词给 8 张表打分（长词权重更高）。
2. 命中表只做**一跳** JOIN 邻居补全，避免无关表通过多跳扩散。
3. 无法识别时回退全部 8 表。
4. 第一次生成只注入检索到的表；校验或执行失败后，第二次重试回退完整 8 表。
5. 提示词同时注入业务日期 `as_of`，避免示例数据与系统日期错位。

检索配置在开发期由 `validate_retrieval_tables()` 自检，必须始终落在 SQL
白名单内。

## 8. 可视化界面

有两种查看方式：

- **应用内“评测看板”**：问答页顶栏按钮打开，直接调用 `/api/eval/*`；
  也可访问 `http://localhost:5171/?eval=1` 直接展开看板。
  - 线上指标：问数 / 助手的任务完成率、工具调用准确率、忠实度、失败总数
    与失败分类。
  - 黄金评测：最近一次 14 个用例的通过率与逐用例明细，可展开查看失败原因、
    未溯源数字与检索表。
  - 最近失败：时间、来源、类别、问题与说明。
  - “运行黄金评测”按钮重新执行用例；写操作回滚、报表文件随后删除，
    且不上报 Langfuse（`push=false`）。
  - 指标为进程内累计，服务重启后清零；长期趋势以 Langfuse 控制台为准。
- **Langfuse 控制台**：开启 `LANGFUSE_ENABLED=true` 并配置凭据后，在 Langfuse
  项目页查看 trace、generation、工具 span、token、延迟与 score 趋势。

看板只读取上述接口，不新增数据库表，也不改变 mock 离线行为。更新代码后需
重启后端才能加载 `/api/eval/*` 路由。

## 9. 不变量

- 不新增第 9 张业务表；指标、失败案例与黄金结果只存在进程内存或本地文件。
- 无登录、无用户体系；评测接口与其他接口一样公开。
- mock 模式下 LLM、ASR、飞书与 Langfuse 均不发起网络请求。
- 日志、接口响应、文档与提交中不得出现 Langfuse 密钥、模型 Key、飞书密钥
  或数据库密码。
- 提示词保留版本文件，不覆盖历史版本。
