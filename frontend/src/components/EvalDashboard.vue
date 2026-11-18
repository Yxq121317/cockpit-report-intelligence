<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  CircleCheck,
  Close,
  DataAnalysis,
  Refresh,
  TrendCharts,
  VideoPlay,
} from '@element-plus/icons-vue'

import {
  fetchEvalFailures,
  fetchEvalSummary,
  fetchGoldenResult,
  runGoldenEvaluation,
  type EvalFailure,
  type EvalSummary,
  type GoldenResult,
} from '@/api/evaluation'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{
  (event: 'update:modelValue', value: boolean): void
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

const summary = ref<EvalSummary | null>(null)
const failures = ref<EvalFailure[]>([])
const golden = ref<GoldenResult | null>(null)
const loading = ref(false)
const running = ref(false)
const error = ref('')
const lastLoadedAt = ref('')

interface KpiCard {
  key: string
  label: string
  value: string
  ratio: number | null
  sub: string
  tone: string
}

interface GoldenMetric {
  key: string
  label: string
  value: number | null
}

const CATEGORY_LABELS: Record<string, string> = {
  clarification: '需要澄清',
  query_failed: '查询失败',
  unfaithful_answer: '忠实度不足',
  tool_call_failed: '工具调用失败',
  assistant_failed: '助手未完成',
}

const INTENT_LABELS: Record<string, string> = {
  data_query: '数据查询',
  data_entry: '数据录入',
  report_generation: '报表生成',
  chitchat: '闲聊',
}

const KIND_LABELS: Record<string, string> = {
  query: '查询',
  entry: '录入',
  report: '报表',
  chitchat: '闲聊',
}

const SCOPE_LABELS: Record<string, string> = {
  query: '问数',
  assistant: '助手',
}

const metrics = computed(() => summary.value?.metrics ?? null)

const kpiCards = computed<KpiCard[]>(() => {
  const queries = metrics.value?.queries
  const assistants = metrics.value?.assistants
  const failureTotal = metrics.value?.failures.total ?? 0
  const queryTotal = queries?.total ?? 0
  const assistantTotal = assistants?.total ?? 0
  const queryRate = queryTotal > 0 ? (queries?.task_completion_rate ?? 0) : null
  const assistantRate =
    assistantTotal > 0 ? (assistants?.task_completion_rate ?? 0) : null
  const queryFaithfulness = queries?.faithfulness_avg ?? null
  const assistantFaithfulness = assistants?.faithfulness_avg ?? null
  const toolAccuracy = assistants?.tool_call_accuracy ?? null

  return [
    {
      key: 'query_completion',
      label: '问数任务完成率',
      value: percent(queryRate),
      ratio: queryRate,
      sub:
        queryTotal > 0
          ? `完成 ${queries?.completed ?? 0} / ${queryTotal} 次问数`
          : '暂无问数请求',
      tone: toneClass(queryRate),
    },
    {
      key: 'query_faithfulness',
      label: '问数忠实度',
      value: percent(queryFaithfulness),
      ratio: queryFaithfulness,
      sub:
        queryTotal > 0
          ? `基于 ${queryTotal} 次回答的数字与日期溯源`
          : '暂无问数请求',
      tone: toneClass(queryFaithfulness),
    },
    {
      key: 'assistant_completion',
      label: '助手任务完成率',
      value: percent(assistantRate),
      ratio: assistantRate,
      sub:
        assistantTotal > 0
          ? `完成 ${assistants?.completed ?? 0} / ${assistantTotal} 次助手请求`
          : '暂无助手请求',
      tone: toneClass(assistantRate),
    },
    {
      key: 'tool_accuracy',
      label: '工具调用准确率',
      value: percent(toolAccuracy),
      ratio: toolAccuracy,
      sub:
        assistants && assistants.tool_calls > 0
          ? `成功 ${assistants.tool_call_success} / ${assistants.tool_calls} 次工具调用`
          : '暂无工具调用',
      tone: toneClass(toolAccuracy),
    },
    {
      key: 'assistant_faithfulness',
      label: '助手忠实度',
      value: percent(assistantFaithfulness),
      ratio: assistantFaithfulness,
      sub:
        assistantTotal > 0
          ? `基于 ${assistantTotal} 次助手回答的数字溯源`
          : '暂无助手请求',
      tone: toneClass(assistantFaithfulness),
    },
    {
      key: 'failures',
      label: '累计失败案例',
      value: String(failureTotal),
      ratio: null,
      sub:
        summary.value?.failure_log_enabled
          ? '进程内保留最近 200 条，并写入本地 JSONL'
          : '进程内保留最近 200 条',
      tone: failureTotal > 0 ? 'is-bad' : 'is-good',
    },
  ]
})

const failureCategories = computed(() =>
  Object.entries(metrics.value?.failures.by_category ?? {})
    .map(([key, count]) => ({ key, label: categoryLabel(key), count }))
    .sort((left, right) => right.count - left.count),
)

const goldenMetrics = computed<GoldenMetric[]>(() => {
  const current = golden.value?.summary
  if (!current) return []
  return [
    { key: 'pass_rate', label: '通过率', value: current.pass_rate },
    { key: 'intent', label: '意图准确率', value: current.intent_accuracy },
    {
      key: 'task',
      label: '任务完成率',
      value: current.task_completion_rate,
    },
    { key: 'tool', label: '工具准确率', value: current.tool_call_accuracy },
    { key: 'faithfulness', label: '忠实度', value: current.faithfulness_avg },
    { key: 'recall', label: '表结构召回率', value: current.schema_recall_avg },
  ]
})

const lastLoadedLabel = computed(() =>
  lastLoadedAt.value
    ? `更新于 ${formatTime(lastLoadedAt.value)} · 每 10 秒自动刷新`
    : '尚未加载',
)

function errorMessage(reason: unknown): string {
  if (reason instanceof Error && reason.message) return reason.message
  return '请求失败，请稍后重试'
}

function percent(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  const pct = value * 100
  return `${Number.isInteger(pct) ? pct.toFixed(0) : pct.toFixed(1)}%`
}

function toneClass(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'is-empty'
  if (value >= 0.99) return 'is-good'
  if (value >= 0.9) return 'is-warn'
  return 'is-bad'
}

function meterWidth(value: number | null): string {
  if (value === null) return '0%'
  const clamped = Math.min(1, Math.max(0, value))
  return `${Math.round(clamped * 100)}%`
}

function formatTime(value?: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
}

function categoryLabel(value: string | null | undefined): string {
  if (!value) return '—'
  return CATEGORY_LABELS[value] ?? value
}

function intentLabel(value: string | null | undefined): string {
  if (!value) return '—'
  return INTENT_LABELS[value] ?? value
}

function kindLabel(value: string): string {
  return KIND_LABELS[value] ?? value
}

function scopeLabel(value: string): string {
  return SCOPE_LABELS[value] ?? value
}

async function loadAll(): Promise<void> {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    const [summaryData, failureData, goldenData] = await Promise.all([
      fetchEvalSummary(),
      fetchEvalFailures(50),
      fetchGoldenResult(),
    ])
    summary.value = summaryData
    failures.value = failureData.items
    golden.value = goldenData
    lastLoadedAt.value = new Date().toISOString()
  } catch (reason) {
    error.value = errorMessage(reason)
  } finally {
    loading.value = false
  }
}

const AUTO_REFRESH_MS = 10_000
let refreshTimer: ReturnType<typeof setInterval> | null = null

function stopAutoRefresh(): void {
  if (refreshTimer !== null) {
    clearInterval(refreshTimer)
    refreshTimer = null
  }
}

function startAutoRefresh(): void {
  stopAutoRefresh()
  refreshTimer = setInterval(() => {
    if (!running.value) void loadAll()
  }, AUTO_REFRESH_MS)
}

async function runGolden(): Promise<void> {
  if (running.value) return
  running.value = true
  try {
    const result = await runGoldenEvaluation()
    golden.value = result
    const current = result.summary
    ElMessage.success(
      current
        ? `黄金评测完成：${current.passed}/${current.total} 通过`
        : '黄金评测完成',
    )
  } catch (reason) {
    ElMessage.error(errorMessage(reason))
  } finally {
    running.value = false
  }
}

watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      void loadAll()
      startAutoRefresh()
    } else {
      stopAutoRefresh()
    }
  },
)

onUnmounted(stopAutoRefresh)
</script>

<template>
  <el-drawer
    v-model="visible"
    class="eval-drawer"
    size="min(1040px, 96vw)"
    title="效果评测看板"
    :with-header="false"
    append-to-body
  >
    <div v-loading="loading" class="eval-panel">
      <header class="dash-head">
        <div class="dash-title">
          <span class="dash-mark">
            <el-icon><TrendCharts /></el-icon>
          </span>
          <div>
            <p class="eyebrow">LLM OBSERVABILITY</p>
            <h2>效果评测看板</h2>
            <p class="dash-sub">
              任务完成率 · 工具调用准确率 · Faithfulness · 失败案例
            </p>
          </div>
        </div>
        <div class="dash-actions">
          <el-button :icon="Refresh" :loading="loading" @click="loadAll">
            刷新
          </el-button>
          <el-button
            circle
            :icon="Close"
            aria-label="关闭评测看板"
            @click="visible = false"
          />
        </div>
      </header>

      <div v-if="error" class="dash-error" role="alert">
        <span>{{ error }}</span>
        <el-button size="small" type="primary" plain @click="loadAll">
          重试
        </el-button>
      </div>

      <section
        class="status-strip"
        :class="summary?.langfuse.enabled ? 'is-on' : 'is-off'"
      >
        <span class="status-dot" aria-hidden="true"></span>
        <div class="status-copy">
          <strong>
            {{
              summary?.langfuse.enabled
                ? 'Langfuse 观测已开启'
                : 'Langfuse 观测未开启'
            }}
          </strong>
          <p v-if="summary?.langfuse.enabled">
            {{ summary.langfuse.host }} · {{ summary.langfuse.environment }} ·
            {{ summary.langfuse.release }}
          </p>
          <p v-else>
            当前看板展示本机进程内指标；在 .env 配置 LANGFUSE_ENABLED 与密钥后，
            可在 Langfuse 控制台查看 trace、token 与长期趋势。
          </p>
        </div>
        <span class="status-meta">
          {{
            summary?.langfuse.enabled
              ? `队列 ${summary.langfuse.queued}`
              : summary?.langfuse.reason || '未启用'
          }}
        </span>
      </section>

      <section class="panel-section">
        <header class="section-title">
          <div>
            <h3>线上指标</h3>
            <p>
              统计自 {{ formatTime(metrics?.since) === '—' ? '服务启动' : formatTime(metrics?.since) }}，
              服务重启后清零
            </p>
          </div>
          <span class="section-badge">{{ lastLoadedLabel }}</span>
        </header>

        <div class="kpi-grid">
          <article
            v-for="card in kpiCards"
            :key="card.key"
            class="kpi-card"
            :class="card.tone"
          >
            <span class="kpi-label">{{ card.label }}</span>
            <strong class="kpi-value">{{ card.value }}</strong>
            <div v-if="card.ratio !== null" class="kpi-meter">
              <span :style="{ width: meterWidth(card.ratio) }"></span>
            </div>
            <p class="kpi-sub">{{ card.sub }}</p>
          </article>
        </div>

        <div v-if="failureCategories.length" class="category-row">
          <span class="category-label">失败分类</span>
          <span
            v-for="item in failureCategories"
            :key="item.key"
            class="category-chip"
            :class="`cat-${item.key}`"
          >
            {{ item.label }} · {{ item.count }}
          </span>
        </div>
      </section>

      <section class="panel-section">
        <header class="section-title">
          <div>
            <h3>黄金评测</h3>
            <p v-if="golden?.available">
              模式 {{ golden.mode }} · {{ formatTime(golden.generated_at) }} ·
              {{ golden.path }}
            </p>
            <p v-else>尚未生成结果；运行后写入本地 JSON，默认完全离线</p>
          </div>
          <el-button
            type="primary"
            plain
            size="small"
            :icon="VideoPlay"
            :loading="running"
            @click="runGolden"
          >
            运行黄金评测
          </el-button>
        </header>

        <div v-if="golden?.available && golden.summary" class="golden-summary">
          <div
            v-for="item in goldenMetrics"
            :key="item.key"
            class="golden-metric"
          >
            <span>{{ item.label }}</span>
            <strong :class="toneClass(item.value)">{{ percent(item.value) }}</strong>
          </div>
        </div>

        <el-table
          v-if="golden?.available && golden.cases.length"
          :data="golden.cases"
          row-key="id"
          size="small"
          class="eval-table"
        >
          <el-table-column type="expand">
            <template #default="{ row }">
              <div class="case-detail">
                <p><strong>问题：</strong>{{ row.question }}</p>
                <p>
                  <strong>实际意图：</strong>{{ intentLabel(row.actual_intent) }}
                  （期望 {{ intentLabel(row.expected_intent) }}）
                </p>
                <p v-if="row.failure">
                  <strong>失败原因：</strong>{{ row.failure }}
                </p>
                <p v-if="row.ungrounded.length">
                  <strong>未溯源数字：</strong>{{ row.ungrounded.join('、') }}
                </p>
                <p>
                  <strong>检索表：</strong>
                  {{ row.retrieved_tables.join('、') || '—' }}
                </p>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="用例" min-width="190">
            <template #default="{ row }">
              <div class="case-name">
                <strong>{{ row.question }}</strong>
                <code>{{ row.id }}</code>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="类型" width="64" align="center">
            <template #default="{ row }">{{ kindLabel(row.kind) }}</template>
          </el-table-column>
          <el-table-column label="意图" width="72" align="center">
            <template #default="{ row }">
              <span
                class="dot-badge"
                :class="row.intent_ok ? 'is-ok' : 'is-bad'"
              >
                {{ row.intent_ok ? '一致' : '偏差' }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="任务" width="68" align="right">
            <template #default="{ row }">{{ percent(row.task_completion) }}</template>
          </el-table-column>
          <el-table-column label="工具" width="68" align="right">
            <template #default="{ row }">{{ percent(row.tool_call_accuracy) }}</template>
          </el-table-column>
          <el-table-column label="忠实度" width="74" align="right">
            <template #default="{ row }">{{ percent(row.faithfulness) }}</template>
          </el-table-column>
          <el-table-column label="召回率" width="74" align="right">
            <template #default="{ row }">{{ percent(row.schema_recall) }}</template>
          </el-table-column>
          <el-table-column label="结果" width="78" align="center">
            <template #default="{ row }">
              <el-tag
                :type="row.passed ? 'success' : 'danger'"
                size="small"
                effect="light"
              >
                {{ row.passed ? '通过' : '未通过' }}
              </el-tag>
            </template>
          </el-table-column>
        </el-table>

        <div v-else-if="!loading" class="empty-state">
          <el-icon><DataAnalysis /></el-icon>
          <strong>还没有黄金评测结果</strong>
          <p>
            14 个用例覆盖查询、录入、报表与闲聊；写操作自动回滚，报表文件随后删除。
          </p>
          <el-button
            type="primary"
            :icon="VideoPlay"
            :loading="running"
            @click="runGolden"
          >
            立即运行
          </el-button>
        </div>
      </section>

      <section class="panel-section">
        <header class="section-title">
          <div>
            <h3>最近失败</h3>
            <p>
              仅记录线上失败请求，内存保留最近 200 条；黄金评测不写入本列表。{{
                summary?.failure_log_enabled
                  ? '已开启本地 JSONL 落盘'
                  : 'EVAL_FAILURE_LOG 未开启，仅保留在进程内'
              }}
            </p>
          </div>
          <span class="section-badge">{{ failures.length }} 条明细</span>
        </header>

        <el-table
          v-if="failures.length"
          :data="failures"
          size="small"
          max-height="340"
          class="eval-table"
        >
          <el-table-column label="时间" width="150">
            <template #default="{ row }">{{ formatTime(row.at) }}</template>
          </el-table-column>
          <el-table-column label="来源" width="64" align="center">
            <template #default="{ row }">{{ scopeLabel(row.scope) }}</template>
          </el-table-column>
          <el-table-column label="类别" width="108">
            <template #default="{ row }">
              <span class="category-chip" :class="`cat-${row.category}`">
                {{ categoryLabel(row.category) }}
              </span>
            </template>
          </el-table-column>
          <el-table-column
            label="问题"
            min-width="180"
            prop="question"
            show-overflow-tooltip
          />
          <el-table-column
            label="说明"
            min-width="220"
            prop="detail"
            show-overflow-tooltip
          />
        </el-table>

        <div v-else-if="!loading" class="empty-inline">
          <el-icon><CircleCheck /></el-icon>
          <span>
            暂无失败案例。线上提问出现需要澄清、SQL 失败、工具调用失败或数字无法溯源时才会记录；
            黄金评测只写入上方评测结果，不计入失败列表。
          </span>
        </div>
      </section>

      <details v-if="summary?.metric_definitions.length" class="defs">
        <summary>指标口径</summary>
        <ul>
          <li
            v-for="definition in summary.metric_definitions"
            :key="definition.key"
          >
            <strong>{{ definition.label }}</strong>
            <span>{{ definition.description }}</span>
          </li>
        </ul>
      </details>

      <p class="dash-foot">
        数据来源：/api/eval/summary · /api/eval/golden · /api/eval/failures
      </p>
    </div>
  </el-drawer>
</template>

<style scoped>
:global(.eval-drawer .el-drawer__body) {
  padding: 0;
  background: var(--p1-canvas);
}

.eval-panel {
  min-height: 100%;
  padding: 22px 24px 30px;
}

.dash-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.dash-title {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.dash-mark {
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  border-radius: 13px;
  font-size: 21px;
  color: #ffffff;
  background: linear-gradient(145deg, #2f6bff, #1f4fd0);
  box-shadow: 0 8px 20px rgba(47, 107, 255, 0.26);
  flex: 0 0 auto;
}

.eyebrow {
  margin: 0 0 3px;
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.18em;
  color: #8b9ab2;
}

.dash-title h2 {
  margin: 0;
  font-size: 19px;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: #0b1b33;
}

.dash-sub {
  margin: 4px 0 0;
  font-size: 10.5px;
  color: #8b9ab2;
}

.dash-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.dash-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 14px;
  padding: 11px 13px;
  border-radius: 11px;
  font-size: 11.5px;
  color: #a34a4a;
  background: #fdf0f0;
  border: 1px solid #f5d2d2;
}

.status-strip {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 16px;
  padding: 13px 15px;
  border-radius: 13px;
  border: 1px solid #dcefe9;
  background: #f6fcfb;
}

.status-strip.is-off {
  border-color: #f3e4b4;
  background: #fdfaf0;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #16b8a6;
  box-shadow: 0 0 0 4px rgba(22, 184, 166, 0.14);
  flex: 0 0 auto;
}

.status-strip.is-off .status-dot {
  background: #d9a514;
  box-shadow: 0 0 0 4px rgba(217, 165, 20, 0.14);
}

.status-copy {
  flex: 1;
  min-width: 0;
}

.status-copy strong {
  font-size: 12px;
  font-weight: 650;
  color: #33425b;
}

.status-copy p {
  margin: 3px 0 0;
  font-size: 10.5px;
  line-height: 1.6;
  color: #7b8aa3;
  overflow-wrap: anywhere;
}

.status-meta {
  padding: 3px 9px;
  border-radius: 999px;
  font-size: 9.5px;
  color: #7b8aa3;
  background: rgba(255, 255, 255, 0.72);
  border: 1px solid #e9eff8;
  white-space: nowrap;
}

.panel-section {
  margin-top: 18px;
  padding: 17px 18px;
  border-radius: 15px;
  background: var(--p1-surface);
  border: 1px solid var(--p1-line);
  box-shadow: var(--p1-shadow-sm);
}

.section-title {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.section-title h3 {
  margin: 0;
  font-size: 13.5px;
  font-weight: 700;
  color: #1f3150;
}

.section-title p {
  margin: 4px 0 0;
  font-size: 10px;
  line-height: 1.55;
  color: #93a1b6;
  overflow-wrap: anywhere;
}

.section-badge {
  padding: 3px 9px;
  border-radius: 999px;
  font-size: 9.5px;
  color: #5b6b84;
  background: #f8fafd;
  border: 1px solid #e9eff8;
  white-space: nowrap;
}

.kpi-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 9px;
  margin-top: 13px;
}

.kpi-card {
  padding: 12px 13px;
  border-radius: 12px;
  background: #fbfcfe;
  border: 1px solid #edf2f9;
}

.kpi-label {
  display: block;
  font-size: 10px;
  font-weight: 600;
  color: #7b8aa3;
}

.kpi-value {
  display: block;
  margin-top: 5px;
  font-size: 23px;
  font-weight: 700;
  letter-spacing: -0.03em;
  font-variant-numeric: tabular-nums;
  color: #33425b;
}

.kpi-card.is-good .kpi-value {
  color: #0f766e;
}

.kpi-card.is-warn .kpi-value {
  color: #8a6200;
}

.kpi-card.is-bad .kpi-value {
  color: #a34a4a;
}

.kpi-card.is-empty .kpi-value {
  color: #a2aec1;
}

.kpi-meter {
  height: 4px;
  margin-top: 8px;
  border-radius: 999px;
  background: #eef2f8;
  overflow: hidden;
}

.kpi-meter span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, #8b9ab2, #b7c4d8);
  transition: width 0.35s ease;
}

.kpi-card.is-good .kpi-meter span {
  background: linear-gradient(90deg, #16b8a6, #5fd7c9);
}

.kpi-card.is-warn .kpi-meter span {
  background: linear-gradient(90deg, #f2a33c, #f6c46e);
}

.kpi-card.is-bad .kpi-meter span {
  background: linear-gradient(90deg, #e5677f, #ef98a9);
}

.kpi-sub {
  margin: 7px 0 0;
  font-size: 9.5px;
  line-height: 1.5;
  color: #93a1b6;
}

.category-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 12px;
}

.category-label {
  font-size: 9.5px;
  font-weight: 600;
  color: #8b9ab2;
}

.category-chip {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 9.5px;
  color: #5b6b84;
  background: #f8fafd;
  border: 1px solid #e9eff8;
  white-space: nowrap;
}

.cat-unfaithful_answer,
.cat-tool_call_failed,
.cat-clarification {
  color: #8a6200;
  background: #fdf7e6;
  border-color: #f3e4b4;
}

.cat-query_failed,
.cat-assistant_failed {
  color: #a34a4a;
  background: #fdf0f0;
  border-color: #f5d2d2;
}

.golden-summary {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 7px;
  margin-top: 13px;
}

.golden-metric {
  padding: 9px 10px;
  border-radius: 10px;
  background: #fbfcfe;
  border: 1px solid #edf2f9;
}

.golden-metric span {
  display: block;
  font-size: 9px;
  color: #93a1b6;
}

.golden-metric strong {
  display: block;
  margin-top: 3px;
  font-size: 15px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: #33425b;
}

.golden-metric strong.is-good {
  color: #0f766e;
}

.golden-metric strong.is-warn {
  color: #8a6200;
}

.golden-metric strong.is-bad {
  color: #a34a4a;
}

.golden-metric strong.is-empty {
  color: #a2aec1;
}

.eval-table {
  margin-top: 13px;
  --el-table-border-color: #edf2f9;
  --el-table-header-bg-color: #f8fafd;
  --el-table-row-hover-bg-color: #f7faff;
}

.case-name {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.case-name strong {
  font-size: 11px;
  font-weight: 600;
  color: #33425b;
  line-height: 1.45;
}

.case-name code {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 9px;
  color: #93a1b6;
}

.dot-badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 7px;
  border-radius: 999px;
  font-size: 9px;
  font-weight: 600;
}

.dot-badge.is-ok {
  color: #0f766e;
  background: #e9f8f5;
  border: 1px solid #bfe9e2;
}

.dot-badge.is-bad {
  color: #a34a4a;
  background: #fdf0f0;
  border: 1px solid #f5d2d2;
}

.case-detail {
  padding: 4px 14px 8px;
  font-size: 10.5px;
  line-height: 1.7;
  color: #62728c;
}

.case-detail p {
  margin: 3px 0;
  overflow-wrap: anywhere;
}

.case-detail strong {
  color: #43526b;
  font-weight: 650;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 7px;
  padding: 30px 16px 24px;
  text-align: center;
}

.empty-state .el-icon {
  font-size: 24px;
  color: #b7c4d8;
}

.empty-state strong {
  font-size: 12px;
  color: #5b6b84;
}

.empty-state p {
  max-width: 430px;
  margin: 0;
  font-size: 10.5px;
  line-height: 1.6;
  color: #93a1b6;
}

.empty-inline {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-top: 12px;
  padding: 12px 13px;
  border-radius: 10px;
  font-size: 10.5px;
  color: #0f766e;
  background: #f6fcfb;
  border: 1px solid #dcefe9;
}

.defs {
  margin-top: 16px;
  padding: 0 2px;
}

.defs summary {
  font-size: 10.5px;
  font-weight: 600;
  color: #5b6b84;
  cursor: pointer;
}

.defs ul {
  margin: 10px 0 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 7px;
}

.defs li {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 10px;
  line-height: 1.6;
  color: #7b8aa3;
}

.defs li strong {
  flex: 0 0 auto;
  font-weight: 650;
  color: #43526b;
}

.dash-foot {
  margin: 16px 2px 0;
  font-size: 9.5px;
  color: #a2aec1;
}

@media (max-width: 900px) {
  .kpi-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .golden-summary {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 720px) {
  :global(.eval-drawer) {
    width: 100% !important;
  }

  .eval-panel {
    padding: 18px 16px 24px;
  }

  .panel-section {
    padding: 15px;
  }

  .dash-actions .el-button:first-child {
    display: none;
  }
}

@media (max-width: 520px) {
  .kpi-grid,
  .golden-summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .status-strip {
    align-items: flex-start;
  }

  .status-meta {
    display: none;
  }
}
</style>
