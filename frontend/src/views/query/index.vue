<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { EChartsOption } from 'echarts'
import {
  ChatDotRound,
  CopyDocument,
  DataAnalysis,
  Document,
  Download,
  Grid,
  Lock,
  Microphone,
  Promotion,
  Search,
  Timer,
  TrendCharts,
} from '@element-plus/icons-vue'

import BaseChart from '@/components/BaseChart.vue'
import EvalDashboard from '@/components/EvalDashboard.vue'
import {
  downloadExport,
  fetchMeta,
  type MetaInfo,
  type QueryResult,
} from '@/api/query'
import {
  chatWithAssistant,
  downloadReport,
  fetchAssistantMeta,
  pushReportToFeishu,
  transcribeAudio,
  type AssistantMeta,
  type AssistantResult,
} from '@/api/assistant'
import { formatCell } from '@/utils/format'
import { WavRecorder } from '@/utils/recorder'

const FALLBACK_EXAMPLES = [
  { question: '上周八年级各班出勤率', category: '出勤' },
  { question: '最近两周迟到次数最多的班级', category: '出勤' },
  { question: '本周待审批请假有多少', category: '请假' },
  { question: '八年级二班本周的课程表', category: '课程' },
  { question: '李雷的家长联系方式', category: '家长' },
  { question: '各年级学生人数', category: '学生' },
  { question: '八年级一班班主任是谁', category: '教师' },
  { question: '本学期老师课时排名', category: '教师' },
]

const SERIES_COLORS = ['#2f6bff', '#16b8a6', '#f2a33c']

const question = ref('')
const asking = ref(false)
const evalOpen = ref(false)
const error = ref('')
const result = ref<QueryResult | null>(null)
const agentResult = ref<AssistantResult | null>(null)
const exporting = ref<'xlsx' | 'csv' | null>(null)
const downloadingReport = ref(false)
const pushingReport = ref(false)
const resultAnchor = ref<HTMLElement | null>(null)
const agentAnchor = ref<HTMLElement | null>(null)

const meta = ref<MetaInfo | null>(null)
const metaError = ref('')
const assistantMeta = ref<AssistantMeta | null>(null)

const recording = ref(false)
const transcribing = ref(false)
const recordSeconds = ref(0)
const recorder = new WavRecorder()
let recordTimer: number | undefined

const examples = computed(() =>
  meta.value?.examples?.length ? meta.value.examples : FALLBACK_EXAMPLES,
)

const llmLabel = computed(() => {
  if (!meta.value) return '模式加载中'
  if (meta.value.llm_mode === 'real') return '在线大模型'
  return meta.value.llm_degraded ? 'Mock 自动降级' : '离线模板模式'
})

const asrLabel = computed(() => {
  const asr = assistantMeta.value?.asr
  if (!asr) return '语音加载中'
  if (asr.mode === 'real') return 'Qwen 语音在线'
  return asr.degraded ? '语音模拟降级' : '语音模拟模式'
})

const asrHint = computed(() => {
  if (recording.value) return `正在聆听 · ${recordSeconds.value}s / 60s`
  if (transcribing.value) return '正在调用 Qwen-ASR 转写…'
  if (!assistantMeta.value) return '语音能力加载中'
  return assistantMeta.value.asr.mode === 'real'
    ? '按麦克风说一句话，自动转成中文问题'
    : '当前为语音模拟模式，转写结果使用固定示例'
})

const agentTitle = computed(() => {
  const current = agentResult.value
  if (!current) return '助手回复'
  if (current.intent === 'report_generation') return '报表已生成'
  if (current.intent === 'data_entry') return '数据录入结果'
  return '助手回复'
})

const chartHeight = computed(() =>
  (result.value?.rows.length ?? 0) > 12 ? '340px' : '268px',
)

const chartOption = computed<EChartsOption>(() => {
  const current = result.value
  if (!current || current.chart.type === 'table' || !current.chart.x) {
    return {}
  }
  const xKey = current.chart.x
  const labels = new Map(
    current.columns.map((column) => [column.key, column.label]),
  )
  const isLine = current.chart.type === 'line'
  const categories = current.rows.map((row) => String(row[xKey] ?? ''))
  const series = current.chart.y.map((key, index) => ({
    name: labels.get(key) ?? key,
    type: isLine ? 'line' : 'bar',
    data: current.rows.map((row) => Number(row[key] ?? 0)),
    showSymbol: false,
    symbol: 'circle',
    symbolSize: 8,
    lineStyle: {
      width: 2,
      color: SERIES_COLORS[index % SERIES_COLORS.length],
    },
    itemStyle: {
      color: SERIES_COLORS[index % SERIES_COLORS.length],
      borderRadius: isLine ? 0 : [5, 5, 0, 0],
    },
    barMaxWidth: 30,
    emphasis: { focus: 'series' },
  }))
  return {
    animationDuration: 450,
    legend:
      series.length > 1
        ? {
            type: 'scroll',
            top: 0,
            left: 0,
            right: 0,
            icon: 'circle',
            itemWidth: 8,
            itemHeight: 8,
            textStyle: { color: '#5b6b84', fontSize: 11 },
          }
        : undefined,
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: isLine ? 'line' : 'shadow' },
      backgroundColor: '#ffffff',
      borderColor: '#e3eaf4',
      padding: [9, 12],
      textStyle: { color: '#33425b', fontSize: 11.5 },
      extraCssText:
        'box-shadow: 0 12px 30px rgba(15, 35, 68, 0.12); border-radius: 10px;',
    },
    grid: {
      left: 4,
      right: 16,
      top: series.length > 1 ? 34 : 16,
      bottom: 4,
      containLabel: true,
    },
    xAxis: {
      type: 'category',
      data: categories,
      axisLine: { lineStyle: { color: '#e3eaf4' } },
      axisTick: { show: false },
      axisLabel: {
        color: '#8b9ab2',
        fontSize: 10.5,
        margin: 11,
        interval: 0,
        hideOverlap: true,
        width: 92,
        overflow: 'truncate',
      },
    },
    yAxis: {
      type: 'value',
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: '#8b9ab2', fontSize: 10.5 },
      splitLine: { lineStyle: { color: '#eef2f8', width: 1 } },
    },
    series,
  } as EChartsOption
})

function errorMessage(reason: unknown): string {
  if (reason instanceof Error && reason.message) return reason.message
  return '请求失败，请稍后重试'
}

async function loadMeta(): Promise<void> {
  try {
    meta.value = await fetchMeta()
    metaError.value = ''
  } catch (reason) {
    metaError.value = errorMessage(reason)
  }
}

async function loadAssistantMeta(): Promise<void> {
  try {
    assistantMeta.value = await fetchAssistantMeta()
  } catch {
    assistantMeta.value = null
  }
}

async function ask(
  text?: string,
  options: { forceReport?: boolean } = {},
): Promise<void> {
  const query = (text ?? question.value).trim()
  if (!query) {
    ElMessage.warning('请先输入要查询的问题')
    return
  }
  if (asking.value) return
  if (recording.value || transcribing.value) {
    ElMessage.warning('请先结束录音或等待转写完成')
    return
  }
  question.value = query
  asking.value = true
  error.value = ''
  try {
    const response = await chatWithAssistant(query, options)
    agentResult.value = response
    result.value = response.query
    await nextTick()
    if (response.query) {
      resultAnchor.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    } else {
      agentAnchor.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  } catch (reason) {
    error.value = errorMessage(reason)
    result.value = null
    agentResult.value = null
  } finally {
    asking.value = false
  }
}

function askExample(example: string): void {
  void ask(example)
}

function generateReportFromQuestion(): void {
  const query = question.value.trim() || '生成校园数据总览模板报表'
  void ask(query, { forceReport: true })
}

async function startRecording(): Promise<void> {
  if (asking.value || transcribing.value || recording.value) return
  try {
    await recorder.start()
    recording.value = true
    recordSeconds.value = 0
    error.value = ''
    recordTimer = window.setInterval(() => {
      recordSeconds.value += 1
      if (recordSeconds.value >= 60) {
        void stopRecording(true)
      }
    }, 1000)
  } catch (reason) {
    recorder.cancel()
    ElMessage.error(errorMessage(reason))
  }
}

async function stopRecording(send = true): Promise<void> {
  if (!recording.value) return
  recording.value = false
  if (recordTimer !== undefined) {
    window.clearInterval(recordTimer)
    recordTimer = undefined
  }
  let audio: Blob
  try {
    audio = await recorder.stop()
  } catch (reason) {
    ElMessage.warning(errorMessage(reason))
    return
  }
  if (!send) return
  transcribing.value = true
  try {
    const asr = await transcribeAudio(audio)
    question.value = asr.text
    if (asr.degraded) {
      ElMessage.warning('语音服务已降级为模拟转写，请确认文本后发送')
    } else {
      ElMessage.success(`已转写：${asr.text}`)
    }
  } catch (reason) {
    ElMessage.error(errorMessage(reason))
  } finally {
    transcribing.value = false
  }
}

function toggleRecording(): void {
  if (recording.value) {
    void stopRecording(true)
  } else {
    void startRecording()
  }
}

function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

async function downloadGeneratedReport(): Promise<void> {
  const report = agentResult.value?.report
  if (!report || downloadingReport.value) return
  downloadingReport.value = true
  try {
    const { blob, filename } = await downloadReport(report.download_url)
    saveBlob(blob, filename)
    ElMessage.success('报表已开始下载')
  } catch (reason) {
    ElMessage.error(errorMessage(reason))
  } finally {
    downloadingReport.value = false
  }
}

async function pushGeneratedReport(): Promise<void> {
  const report = agentResult.value?.report
  if (!report || pushingReport.value) return
  pushingReport.value = true
  try {
    const result = await pushReportToFeishu(report.report_id)
    ElMessage.success(
      result.mode === 'mock'
        ? '已模拟推送到飞书群（未配置应用凭据）'
        : `已推送到飞书群：${result.title}`,
    )
  } catch (reason) {
    ElMessage.error(errorMessage(reason))
  } finally {
    pushingReport.value = false
  }
}

async function copySql(): Promise<void> {
  if (!result.value?.sql) return
  try {
    await navigator.clipboard.writeText(result.value.sql)
    ElMessage.success('SQL 已复制')
  } catch {
    ElMessage.warning('当前环境无法访问剪贴板，请手动选择 SQL')
  }
}

async function exportResult(format: 'xlsx' | 'csv'): Promise<void> {
  if (!result.value || !result.value.rows.length) {
    ElMessage.warning('当前结果没有可导出的数据')
    return
  }
  exporting.value = format
  try {
    const { blob, filename } = await downloadExport({
      format,
      filename: `校园数据_${result.value.title}`,
      columns: result.value.columns.map((column) => ({
        key: column.key,
        label: column.label,
      })),
      rows: result.value.rows,
    })
    saveBlob(
      blob,
      filename.endsWith(`.${format}`) ? filename : `${filename}.${format}`,
    )
    ElMessage.success('导出已开始下载')
  } catch (reason) {
    ElMessage.error(errorMessage(reason))
  } finally {
    exporting.value = null
  }
}

onMounted(() => {
  void loadMeta()
  void loadAssistantMeta()
  // 支持通过 ?eval=1 直接打开评测看板，便于分享与演示。
  if (new URLSearchParams(window.location.search).get('eval') === '1') {
    evalOpen.value = true
  }
})

onBeforeUnmount(() => {
  if (recordTimer !== undefined) {
    window.clearInterval(recordTimer)
  }
  recorder.cancel()
})
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <div class="brand">
        <span class="brand-mark">问</span>
        <div class="brand-copy">
          <strong>校园数据问答</strong>
          <span>Text2SQL</span>
        </div>
      </div>
      <div class="top-stats">
        <span class="stat">
          <i class="stat-dot"></i>
          {{ meta?.database ?? '数据库' }}
        </span>
        <span class="stat">{{ meta?.table_count ?? 8 }} 张业务表</span>
        <span class="stat" :class="{ 'is-mock': meta?.llm_mode !== 'real' }">
          {{ llmLabel }}
        </span>
        <span
          class="stat"
          :class="{ 'is-mock': assistantMeta?.asr.mode !== 'real' }"
        >
          {{ asrLabel }}
        </span>
        <el-button
          class="eval-entry"
          size="small"
          :icon="TrendCharts"
          @click="evalOpen = true"
        >
          评测看板
        </el-button>
      </div>
    </header>

    <EvalDashboard v-model="evalOpen" />

    <main class="page">
      <section class="hero">
        <div class="hero-copy">
          <p class="eyebrow">CAMPUS DATA · ASK IN CHINESE</p>
          <h1>用一句话，查校园数据</h1>
          <p class="lead">
            覆盖学生、教师、班级、年级、家长、出勤、请假与课程表。
            系统自动生成只读 SQL，并给出中文回答、图表与可导出的明细。
          </p>
          <div class="hero-tags">
            <span><el-icon><Lock /></el-icon>只读安全</span>
            <span><el-icon><Grid /></el-icon>严格 8 张表</span>
            <span><el-icon><Timer /></el-icon>3 秒超时</span>
            <span><el-icon><Download /></el-icon>XLSX / CSV</span>
          </div>
        </div>
        <div class="hero-panel">
          <span class="panel-label">当前数据源</span>
          <strong>{{ meta?.database ?? '加载中' }}</strong>
          <p class="panel-sub">
            {{ llmLabel }}<br />
            单次最多返回 {{ meta?.max_rows ?? 200 }} 行
          </p>
        </div>
      </section>

      <div class="workspace">
        <div class="main-column">
          <section class="ask-card">
        <div class="ask-head">
          <span class="ask-icon">
            <el-icon><ChatDotRound /></el-icon>
          </span>
          <div>
            <h2>想问什么？</h2>
            <p>可以打字，也可以按麦克风说话；系统先识别意图再执行</p>
          </div>
        </div>

        <el-input
          v-model="question"
          type="textarea"
          :rows="3"
          maxlength="500"
          show-word-limit
          resize="none"
          placeholder="例如：上周八年级各班出勤率"
          class="ask-input"
          @keydown.enter.exact.prevent="ask()"
        />

        <div class="example-row">
          <button
            v-for="example in examples"
            :key="example.question"
            type="button"
            class="example-chip"
            :title="example.category"
            @click="askExample(example.question)"
          >
            {{ example.question }}
          </button>
        </div>

        <div class="ask-actions">
          <div class="ask-tools">
            <el-button
              size="large"
              type="primary"
              plain
              :icon="Document"
              :disabled="asking || recording || transcribing"
              title="根据输入框中的问题生成对应报表；留空则生成校园数据总览模板"
              @click="generateReportFromQuestion"
            >
              按问题生成报表
            </el-button>
            <el-button
              size="large"
              :type="recording ? 'danger' : 'default'"
              :icon="Microphone"
              :loading="transcribing"
              :disabled="asking"
              @click="toggleRecording"
            >
              {{
                recording
                  ? `结束录音 ${recordSeconds}s`
                  : transcribing
                    ? '正在转写'
                    : '语音输入'
              }}
            </el-button>
            <span class="ask-hint">{{ asrHint }}</span>
          </div>
          <div class="ask-submit">
            <span class="ask-hint">Enter 发送 · Shift + Enter 换行</span>
            <el-button
              type="primary"
              size="large"
              :icon="Search"
              :loading="asking"
              :disabled="recording || transcribing"
              @click="ask()"
            >
              开始处理
            </el-button>
          </div>
        </div>
      </section>

      <div v-if="error" class="error-banner" role="alert">
        <strong>处理失败</strong>
        <span>{{ error }}</span>
        <el-button type="primary" plain size="small" @click="ask()">重试</el-button>
      </div>

      <section
        v-if="agentResult && !agentResult.query"
        ref="agentAnchor"
        class="agent-card"
        aria-live="polite"
      >
        <header class="agent-head">
          <div class="agent-title">
            <span
              class="intent-badge"
              :class="`intent-${agentResult.intent}`"
            >
              {{ agentResult.intent_label }}
            </span>
            <h2>{{ agentTitle }}</h2>
          </div>
          <div class="meta-chips">
            <span class="meta-chip">{{ agentResult.elapsed_ms }} ms</span>
            <span
              class="meta-chip"
              :class="{ 'is-mock': agentResult.degraded }"
            >
              {{
                agentResult.degraded
                  ? '降级模式'
                  : agentResult.model_mode === 'real'
                    ? '大模型决策'
                    : '离线规则'
              }}
            </span>
          </div>
        </header>

        <p class="agent-reply">{{ agentResult.reply }}</p>

        <div v-if="agentResult.tool_calls.length" class="tool-list">
          <div
            v-for="(call, index) in agentResult.tool_calls"
            :key="`${call.name}-${index}`"
            class="tool-item"
            :class="{ 'is-error': call.status !== 'success' }"
          >
            <span class="tool-index">{{ index + 1 }}</span>
            <div class="tool-copy">
              <strong>{{ call.label }}</strong>
              <p>{{ call.summary }}</p>
            </div>
            <span class="tool-status">
              {{ call.status === 'success' ? '已执行' : '未完成' }}
            </span>
          </div>
        </div>

        <div v-if="agentResult.report" class="report-card">
          <div class="report-copy">
            <span class="report-label">XLSX 报表</span>
            <strong>{{ agentResult.report.title }}</strong>
            <p>
              {{ agentResult.report.row_count }} 行数据 · 中文表头 · 冻结首行
              <template v-if="agentResult.report.sheets?.length">
                · {{ agentResult.report.sheets.length }} 个工作表
              </template>
            </p>
          </div>
          <div class="report-actions">
            <el-button
              :icon="Promotion"
              :loading="pushingReport"
              @click="pushGeneratedReport"
            >
              推送到飞书
            </el-button>
            <el-button
              type="primary"
              :icon="Download"
              :loading="downloadingReport"
              @click="downloadGeneratedReport"
            >
              下载报表
            </el-button>
          </div>
        </div>

        <p class="agent-reason">
          意图判断：{{ agentResult.intent_reason }}（置信度
          {{ Math.round(agentResult.intent_confidence * 100) }}%）
        </p>
      </section>

      <section
        v-if="result"
        ref="resultAnchor"
        v-loading="asking"
        class="answer-card"
        aria-live="polite"
      >
        <header class="answer-head">
          <div class="answer-title">
            <span class="ai-badge">
              {{
                result.need_clarification
                  ? '需要补充'
                  : result.model_mode === 'real'
                    ? 'AI 生成'
                    : '模板生成'
              }}
            </span>
            <h2>{{ result.title }}</h2>
          </div>
          <div class="meta-chips">
            <span
              v-if="agentResult"
              class="meta-chip intent-chip"
              :class="`intent-${agentResult.intent}`"
            >
              {{ agentResult.intent_label }}
            </span>
            <span class="meta-chip">{{ result.row_count }} 行</span>
            <span class="meta-chip">{{ result.elapsed_ms }} ms</span>
            <span class="meta-chip" :class="{ 'is-mock': result.degraded }">
              {{ result.degraded ? '降级模式' : llmLabel }}
            </span>
          </div>
        </header>

        <div class="answer-text">{{ result.answer }}</div>
        <p v-if="result.explanation" class="explanation">
          {{ result.explanation }}
        </p>

        <template v-if="!result.need_clarification">
          <div
            v-if="result.chart.type !== 'table' && result.chart.x && result.rows.length"
            class="chart-card"
          >
            <header class="section-head">
              <h3>自动图表</h3>
              <span>{{ result.chart.type === 'line' ? '趋势' : '对比' }}</span>
            </header>
            <BaseChart
              :option="chartOption"
              :empty="!result.rows.length"
              :height="chartHeight"
            />
          </div>

          <details v-if="result.sql" class="sql-block">
            <summary>
              <span>查看生成的 SQL</span>
              <button type="button" class="copy-btn" @click.prevent="copySql">
                <el-icon><CopyDocument /></el-icon>
                复制
              </button>
            </summary>
            <pre>{{ result.sql }}</pre>
          </details>

          <div class="data-card">
            <header class="section-head data-head">
              <div>
                <h3>结果明细</h3>
                <span>最多返回 {{ meta?.max_rows ?? 200 }} 行</span>
              </div>
              <div class="table-actions">
                <el-button
                  size="small"
                  :icon="Download"
                  :loading="exporting === 'xlsx'"
                  :disabled="!result.rows.length"
                  @click="exportResult('xlsx')"
                >
                  XLSX
                </el-button>
                <el-button
                  size="small"
                  :icon="Download"
                  :loading="exporting === 'csv'"
                  :disabled="!result.rows.length"
                  @click="exportResult('csv')"
                >
                  CSV
                </el-button>
              </div>
            </header>

            <div class="table-wrap">
              <el-table
                v-if="result.rows.length"
                :data="result.rows"
                size="small"
                class="query-table"
                max-height="420"
                border
              >
                <el-table-column
                  v-for="column in result.columns"
                  :key="column.key"
                  :label="column.label"
                  :min-width="column.type === 'number' ? 108 : 142"
                  :align="column.type === 'number' ? 'right' : 'left'"
                >
                  <template #default="{ row }">
                    <span :class="{ 'numeric-cell': column.type === 'number' }">
                      {{ formatCell(row[column.key], column.type) }}
                    </span>
                  </template>
                </el-table-column>
              </el-table>
              <div v-else class="empty-data">
                <el-icon><DataAnalysis /></el-icon>
                <strong>查询成功，但没有匹配的数据</strong>
                <p>可尝试放宽时间范围或更换筛选条件</p>
              </div>
            </div>
          </div>
        </template>

        <footer class="answer-foot">
          <span>数字直接来自当前数据库 · 单条 SELECT 只读查询</span>
          <span>AI 生成，请复核后使用</span>
        </footer>
      </section>

      <section v-else-if="!asking && !agentResult" class="empty-card">
        <span class="empty-icon">
          <el-icon><Search /></el-icon>
        </span>
        <h2>从一句话开始</h2>
        <p>
          输入问题或按麦克风说话；查询会生成只读 SQL，录入与报表由
          Function Call 自动调用内部工具。8 张业务表已就绪，无需登录。
        </p>
        <div class="empty-points">
          <span>语音转写</span>
          <span>意图识别</span>
          <span>自然语言回答</span>
          <span>自动图表</span>
          <span>明细可导出</span>
          <span>报表可下载</span>
        </div>
      </section>

        </div>

        <aside class="schema-panel">
        <header class="schema-head">
          <div>
            <h2>数据表</h2>
            <p>{{ meta?.table_count ?? 8 }} 张业务表 · 点击展开字段</p>
          </div>
          <el-icon class="schema-icon"><Grid /></el-icon>
        </header>

        <div v-if="metaError" class="schema-error">
          {{ metaError }}
        </div>

        <details
          v-for="(table, index) in meta?.tables ?? []"
          :key="table.name"
          class="table-item"
          :open="index < 2"
        >
          <summary>
            <span class="table-label">{{ table.label }}</span>
            <code>{{ table.name }}</code>
            <span class="column-count">{{ table.columns.length }} 字段</span>
          </summary>
          <p>{{ table.description }}</p>
          <ul>
            <li v-for="column in table.columns" :key="column.name">
              <code>{{ column.name }}</code>
              <span>{{ column.label }}</span>
            </li>
          </ul>
        </details>

        <div v-if="!meta && !metaError" class="schema-loading">
          正在读取表结构…
        </div>
        </aside>
      </div>
    </main>

    <footer class="page-foot">
      <span>校园数据问答 · 8 张业务表</span>
      <span>只读 SQL · 表白名单 · 自动 LIMIT</span>
    </footer>
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
  overflow-x: hidden;
  background:
    radial-gradient(circle at 12% -8%, rgba(47, 107, 255, 0.12), transparent 34%),
    radial-gradient(circle at 92% 2%, rgba(22, 184, 166, 0.08), transparent 28%),
    var(--p1-canvas);
}

.topbar {
  position: sticky;
  top: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 13px max(20px, calc((100vw - 1280px) / 2 + 20px));
  background: rgba(243, 246, 251, 0.82);
  border-bottom: 1px solid rgba(227, 234, 244, 0.85);
  backdrop-filter: blur(14px);
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.brand-mark {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  border-radius: 9px;
  font-size: 14px;
  font-weight: 700;
  color: #ffffff;
  background: linear-gradient(145deg, #2f6bff, #1f4fd0);
  box-shadow: 0 6px 16px rgba(47, 107, 255, 0.25);
  flex: 0 0 auto;
}

.brand-copy {
  display: flex;
  align-items: baseline;
  gap: 7px;
  min-width: 0;
}

.brand-copy strong {
  font-size: 14px;
  letter-spacing: -0.01em;
  white-space: nowrap;
}

.brand-copy span {
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.08em;
  color: #8b9ab2;
  text-transform: uppercase;
}

.top-stats {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 8px;
  min-width: 0;
}

.stat {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 9px;
  border-radius: 999px;
  font-size: 10.5px;
  color: #5b6b84;
  background: rgba(255, 255, 255, 0.72);
  border: 1px solid #e3eaf4;
  white-space: nowrap;
}

.stat.is-mock {
  color: #8a6200;
  background: #fdf7e6;
  border-color: #f3e4b4;
}

.stat-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #16b8a6;
}

.eval-entry {
  border-radius: 999px;
  font-size: 10.5px;
  color: #2f5fb3;
  background: #eef3ff;
  border-color: #c8d7ff;
}

.eval-entry:hover {
  color: #1f4fd0;
  background: #e4edff;
  border-color: #9db8ff;
}

.page {
  width: 100%;
  max-width: 1280px;
  margin: 0 auto;
  padding: 30px 20px 18px;
}

.hero {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 238px;
  gap: 22px;
  align-items: stretch;
}

.hero-copy {
  min-width: 0;
}

.eyebrow {
  margin: 0 0 11px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.2em;
  color: #8b9ab2;
}

.hero h1 {
  max-width: 720px;
  margin: 0;
  font-size: clamp(28px, 4.2vw, 46px);
  font-weight: 750;
  line-height: 1.12;
  letter-spacing: -0.035em;
  color: #0b1b33;
}

.lead {
  max-width: 680px;
  margin: 13px 0 0;
  font-size: 13px;
  line-height: 1.75;
  color: #62728c;
}

.hero-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  margin-top: 17px;
}

.hero-tags span {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 5px 9px;
  border-radius: 999px;
  font-size: 10.5px;
  color: #43526b;
  background: rgba(255, 255, 255, 0.7);
  border: 1px solid #e3eaf4;
}

.hero-panel {
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 18px;
  border-radius: 18px;
  color: #dfe8f5;
  background:
    radial-gradient(circle at 88% 0%, rgba(47, 107, 255, 0.4), transparent 48%),
    linear-gradient(145deg, #0d1f39, #102946);
  box-shadow: 0 18px 48px rgba(15, 35, 68, 0.18);
}

.panel-label {
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.12em;
  color: #8fa6c4;
  text-transform: uppercase;
}

.hero-panel strong {
  margin-top: 7px;
  font-size: 22px;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: #ffffff;
}

.panel-sub {
  margin: 8px 0 0;
  font-size: 10.5px;
  line-height: 1.7;
  color: #a9bad3;
}

.ask-card,
.answer-card,
.agent-card,
.empty-card,
.schema-panel {
  border-radius: 18px;
  background: var(--p1-surface);
  border: 1px solid var(--p1-line);
  box-shadow: var(--p1-shadow-sm);
}

.ask-card {
  margin-top: 22px;
  padding: 20px;
}

.ask-head {
  display: flex;
  align-items: center;
  gap: 11px;
}

.ask-icon {
  display: grid;
  place-items: center;
  width: 38px;
  height: 38px;
  border-radius: 11px;
  font-size: 19px;
  color: #2f6bff;
  background: #eef3ff;
  flex: 0 0 auto;
}

.ask-head h2,
.answer-head h2,
.schema-head h2,
.empty-card h2 {
  margin: 0;
  font-size: 16px;
  font-weight: 650;
  letter-spacing: -0.01em;
}

.ask-head p,
.schema-head p {
  margin: 4px 0 0;
  font-size: 11px;
  color: #8b9ab2;
}

.ask-input {
  margin-top: 14px;
}

.ask-input :deep(.el-textarea__inner) {
  min-height: 88px !important;
  padding: 12px 13px;
  font-size: 13px;
  line-height: 1.65;
  border-radius: 11px;
  box-shadow: 0 0 0 1px #e3eaf4 inset;
}

.example-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 11px;
}

.example-chip {
  padding: 5px 10px;
  border-radius: 999px;
  font-size: 10.5px;
  color: #5b6b84;
  background: #f8fafd;
  border: 1px solid #e9eff8;
  cursor: pointer;
  transition: color 0.16s ease, border-color 0.16s ease, background 0.16s ease;
}

.example-chip:hover {
  color: #2f6bff;
  background: #eef3ff;
  border-color: #c8d7ff;
}

.ask-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 14px;
}

.ask-tools,
.ask-submit {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.ask-tools .el-button {
  flex: 0 0 auto;
}

.ask-submit {
  justify-content: flex-end;
}

.ask-hint {
  font-size: 10.5px;
  color: #93a1b6;
}

.error-banner {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 14px;
  padding: 12px 14px;
  border-radius: 12px;
  font-size: 12px;
  color: #8a2b2b;
  background: #fdf0f0;
  border: 1px solid #f5d2d2;
}

.error-banner span {
  flex: 1;
  color: #a34a4a;
}

.answer-card,
.agent-card,
.empty-card {
  min-height: 240px;
  margin-top: 15px;
  padding: 20px;
}

.agent-card {
  min-height: 0;
}

.agent-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.agent-title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.agent-title h2 {
  margin: 0;
  font-size: 16px;
  font-weight: 650;
  letter-spacing: -0.01em;
}

.intent-badge {
  padding: 3px 8px;
  border-radius: 6px;
  font-size: 9.5px;
  font-weight: 700;
  letter-spacing: 0.04em;
  color: #2f5fb3;
  background: #eef3ff;
  border: 1px solid #c8d7ff;
  flex: 0 0 auto;
}

.intent-badge.intent-data_entry,
.meta-chip.intent-chip.intent-data_entry {
  color: #0f766e;
  background: #e9f8f5;
  border-color: #bfe9e2;
}

.intent-badge.intent-report_generation,
.meta-chip.intent-chip.intent-report_generation {
  color: #8a6200;
  background: #fdf7e6;
  border-color: #f3e4b4;
}

.intent-badge.intent-chitchat,
.meta-chip.intent-chip.intent-chitchat {
  color: #5b6b84;
  background: #f4f6fa;
  border-color: #e3e9f2;
}

.meta-chip.intent-chip {
  color: #2f5fb3;
  background: #eef3ff;
  border-color: #c8d7ff;
  font-weight: 600;
}

.agent-reply {
  margin: 14px 0 0;
  padding: 13px 15px;
  border-radius: 12px;
  font-size: 13.5px;
  line-height: 1.75;
  color: #1f3150;
  background: linear-gradient(135deg, #f7faff, #f2f7ff);
  border: 1px solid #e4edff;
  overflow-wrap: anywhere;
}

.tool-list {
  display: flex;
  flex-direction: column;
  gap: 7px;
  margin-top: 14px;
}

.tool-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 10px;
  background: #f8fbfa;
  border: 1px solid #dcefe9;
}

.tool-item.is-error {
  background: #fdf5f5;
  border-color: #f2d8d8;
}

.tool-index {
  display: grid;
  place-items: center;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  font-size: 10px;
  font-weight: 700;
  color: #0f766e;
  background: #dff3ee;
  flex: 0 0 auto;
}

.tool-item.is-error .tool-index {
  color: #a34a4a;
  background: #f8dddd;
}

.tool-copy {
  flex: 1;
  min-width: 0;
}

.tool-copy strong {
  font-size: 11.5px;
  font-weight: 650;
  color: #33425b;
}

.tool-copy p {
  margin: 3px 0 0;
  font-size: 10.5px;
  line-height: 1.55;
  color: #6f7f96;
  overflow-wrap: anywhere;
}

.tool-status {
  font-size: 10px;
  font-weight: 600;
  color: #0f766e;
  white-space: nowrap;
}

.tool-item.is-error .tool-status {
  color: #a34a4a;
}

.report-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-top: 14px;
  padding: 14px;
  border-radius: 12px;
  background:
    radial-gradient(circle at 96% 0%, rgba(47, 107, 255, 0.14), transparent 40%),
    #f7faff;
  border: 1px solid #dce8ff;
}

.report-copy {
  min-width: 0;
}

.report-label {
  display: inline-block;
  margin-bottom: 4px;
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: #2f6bff;
}

.report-copy strong {
  display: block;
  font-size: 13px;
  color: #1f3150;
  overflow-wrap: anywhere;
}

.report-copy p {
  margin: 4px 0 0;
  font-size: 10.5px;
  color: #7b8aa3;
}

.report-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.report-actions .el-button + .el-button {
  margin-left: 0;
}

.agent-reason {
  margin: 12px 0 0;
  font-size: 10px;
  line-height: 1.6;
  color: #93a1b6;
}

.answer-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.answer-title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.answer-title h2 {
  overflow-wrap: anywhere;
}

.ai-badge {
  padding: 3px 7px;
  border-radius: 6px;
  font-size: 9.5px;
  font-weight: 700;
  letter-spacing: 0.04em;
  color: #6b4dc7;
  background: #f2eefb;
  border: 1px solid #e2dbf6;
  flex: 0 0 auto;
}

.meta-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.meta-chip {
  padding: 3px 8px;
  border-radius: 999px;
  font-size: 10px;
  color: #5b6b84;
  background: #f8fafd;
  border: 1px solid #e9eff8;
  white-space: nowrap;
}

.meta-chip.is-mock {
  color: #8a6200;
  background: #fdf7e6;
  border-color: #f3e4b4;
}

.answer-text {
  margin-top: 15px;
  padding: 14px 16px;
  border-radius: 12px;
  font-size: 14px;
  font-weight: 500;
  line-height: 1.8;
  color: #1f3150;
  background: linear-gradient(135deg, #f7faff, #f2f7ff);
  border: 1px solid #e4edff;
  overflow-wrap: anywhere;
}

.explanation {
  margin: 8px 2px 0;
  font-size: 11px;
  line-height: 1.65;
  color: #7b8aa3;
}

.chart-card,
.data-card {
  margin-top: 16px;
  padding-top: 15px;
  border-top: 1px solid #f0f4fa;
}

.section-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}

.section-head h3 {
  margin: 0;
  font-size: 12.5px;
  font-weight: 650;
  color: #43526b;
}

.section-head span {
  font-size: 10px;
  color: #93a1b6;
}

.sql-block {
  margin-top: 14px;
  border-radius: 10px;
  background: #0b1b33;
  overflow: hidden;
}

.sql-block summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 9px 13px;
  font-size: 10.5px;
  font-weight: 600;
  color: #a9bad3;
  cursor: pointer;
  list-style: none;
}

.sql-block summary::-webkit-details-marker {
  display: none;
}

.copy-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 7px;
  border: 1px solid rgba(255, 255, 255, 0.16);
  border-radius: 6px;
  font-size: 10px;
  color: #c5d3e8;
  background: rgba(255, 255, 255, 0.06);
  cursor: pointer;
}

.copy-btn:hover {
  color: #ffffff;
}

.sql-block pre {
  margin: 0;
  padding: 0 13px 12px;
  overflow-x: auto;
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 11px;
  line-height: 1.7;
  color: #dfe8f5;
  white-space: pre-wrap;
  word-break: break-all;
}

.data-head {
  align-items: flex-end;
}

.table-actions {
  display: flex;
  align-items: center;
  gap: 6px;
}

.table-wrap {
  width: 100%;
  overflow-x: auto;
}

.query-table {
  width: 100%;
}

.numeric-cell {
  font-variant-numeric: tabular-nums;
}

.empty-data {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 34px 16px;
  text-align: center;
  color: #8b9ab2;
}

.empty-data .el-icon {
  font-size: 22px;
  color: #b7c4d8;
}

.empty-data strong {
  font-size: 12px;
  font-weight: 600;
  color: #5b6b84;
}

.empty-data p {
  margin: 0;
  font-size: 10.5px;
}

.answer-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid #f2f5fa;
  font-size: 10px;
  color: #93a1b6;
}

.empty-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 240px;
  text-align: center;
}

.empty-icon {
  display: grid;
  place-items: center;
  width: 46px;
  height: 46px;
  border-radius: 13px;
  font-size: 21px;
  color: #2f6bff;
  background: #eef3ff;
}

.empty-card h2 {
  margin-top: 12px;
}

.empty-card p {
  max-width: 430px;
  margin: 7px 0 0;
  font-size: 11.5px;
  line-height: 1.7;
  color: #8b9ab2;
}

.empty-points {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 6px;
  margin-top: 14px;
}

.empty-points span {
  padding: 3px 8px;
  border-radius: 999px;
  font-size: 10px;
  color: #5b6b84;
  background: #f8fafd;
  border: 1px solid #e9eff8;
}

.schema-panel {
  margin-top: 15px;
  padding: 18px;
}

.schema-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}

.schema-icon {
  font-size: 18px;
  color: #b7c4d8;
}

.schema-error {
  margin-top: 12px;
  padding: 9px 10px;
  border-radius: 9px;
  font-size: 10.5px;
  line-height: 1.55;
  color: #a34a4a;
  background: #fdf0f0;
  border: 1px solid #f5d2d2;
}

.schema-loading {
  margin-top: 12px;
  font-size: 10.5px;
  color: #93a1b6;
}

.table-item {
  margin-top: 9px;
  border-radius: 10px;
  background: #fbfcfe;
  border: 1px solid #edf2f9;
  overflow: hidden;
}

.table-item summary {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 9px 10px;
  cursor: pointer;
  list-style: none;
}

.table-item summary::-webkit-details-marker {
  display: none;
}

.table-label {
  font-size: 11.5px;
  font-weight: 650;
  color: #33425b;
}

.table-item code {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 9.5px;
  color: #7b8aa3;
}

.column-count {
  margin-left: auto;
  font-size: 9px;
  color: #a2aec1;
  white-space: nowrap;
}

.table-item p {
  margin: 0;
  padding: 0 10px 7px;
  font-size: 9.5px;
  line-height: 1.55;
  color: #8b9ab2;
}

.table-item ul {
  display: flex;
  flex-direction: column;
  gap: 3px;
  margin: 0;
  padding: 0 10px 10px;
  list-style: none;
}

.table-item li {
  display: flex;
  align-items: baseline;
  gap: 7px;
  font-size: 9.5px;
  color: #62728c;
}

.table-item li code {
  min-width: 86px;
  color: #2f5fb3;
}

.page-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
  max-width: 1280px;
  margin: 0 auto;
  padding: 6px 20px 26px;
  font-size: 10px;
  color: #9aa8bd;
}

@media (min-width: 1080px) {
  .ask-card {
    margin-top: 0;
  }

  .schema-panel {
    position: sticky;
    top: 76px;
    margin-top: 0;
    max-height: calc(100vh - 92px);
    overflow-y: auto;
  }

  .workspace {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 318px;
    gap: 15px;
    align-items: start;
    margin-top: 22px;
  }

  .main-column {
    min-width: 0;
  }
}

@media (max-width: 1079px) {
  .workspace {
    display: block;
    margin-top: 0;
  }

  .schema-panel {
    margin-top: 15px;
  }
}

@media (max-width: 860px) {
  .hero {
    grid-template-columns: 1fr;
  }

  .hero-panel {
    flex-direction: row;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
  }

  .hero-panel strong {
    margin-top: 0;
  }

  .panel-sub {
    margin: 0;
    text-align: right;
  }
}

@media (max-width: 680px) {
  .topbar {
    padding: 11px 16px;
  }

  .top-stats .stat:nth-child(2),
  .top-stats .stat:nth-child(4) {
    display: none;
  }

  .page {
    padding: 22px 16px 14px;
  }

  .page-foot {
    padding: 4px 16px 22px;
  }

  .hero h1 {
    font-size: clamp(27px, 9vw, 36px);
  }

  .ask-card,
  .answer-card,
  .agent-card,
  .empty-card,
  .schema-panel {
    padding: 16px;
  }

  .ask-actions,
  .ask-tools,
  .ask-submit {
    align-items: stretch;
    flex-direction: column;
    width: 100%;
  }

  .ask-actions .el-button,
  .ask-tools .el-button,
  .ask-submit .el-button {
    width: 100%;
    margin-left: 0;
  }

  .ask-hint {
    text-align: center;
  }

  .report-card {
    align-items: stretch;
    flex-direction: column;
  }

  .report-actions {
    width: 100%;
  }

  .report-actions .el-button {
    flex: 1;
  }

  .report-card .el-button {
    width: 100%;
    margin-left: 0;
  }

  .error-banner {
    align-items: flex-start;
    flex-wrap: wrap;
  }

  .error-banner span {
    flex-basis: 100%;
  }

  .answer-text {
    padding: 12px 13px;
    font-size: 13px;
  }

  .data-head {
    align-items: flex-start;
    flex-direction: column;
  }

  .table-actions {
    width: 100%;
  }

  .table-actions .el-button {
    flex: 1;
    margin-left: 0;
  }

  .answer-foot {
    align-items: flex-start;
    flex-direction: column;
    gap: 4px;
  }

  .hero-panel {
    flex-direction: column;
    align-items: flex-start;
  }

  .panel-sub {
    text-align: left;
  }
}
</style>
