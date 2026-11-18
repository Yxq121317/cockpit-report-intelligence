import { API_BASE, ApiError, request } from './http'
import type { QueryColumn, QueryResult, QueryRow } from './query'

export interface AgentToolCall {
  name: string
  label: string
  arguments: Record<string, unknown>
  status: 'success' | 'error'
  summary: string
  data: Record<string, unknown>
}

export interface ReportSheet {
  name: string
  row_count: number
}

export interface ReportInfo {
  report_id: string
  title: string
  filename: string
  download_url: string
  row_count: number
  columns: QueryColumn[]
  rows: QueryRow[]
  sheets?: ReportSheet[]
  file_path?: string
}

export interface AssistantResult {
  question: string
  intent: 'data_query' | 'data_entry' | 'report_generation' | 'chitchat'
  intent_label: string
  intent_description: string
  intent_confidence: number
  intent_reason: string
  reply: string
  tool_calls: AgentToolCall[]
  query: QueryResult | null
  report: ReportInfo | null
  degraded: boolean
  model_mode: 'mock' | 'real'
  elapsed_ms: number
}

export interface AsrInfo {
  mode: 'mock' | 'real'
  degraded: boolean
  model: string
  language: string
  sample_rate: number
  max_seconds: number
}

export interface AssistantToolMeta {
  name: string
  label: string
  description: string
}

export interface AssistantMeta {
  intents: Array<{ key: string; label: string; description: string }>
  tools: AssistantToolMeta[]
  examples: Array<{ question: string; category: string }>
  asr: AsrInfo
  llm_mode: 'mock' | 'real'
  llm_degraded: boolean
}

export interface AsrResult {
  text: string
  mode: 'mock' | 'real'
  degraded: boolean
  model: string
  elapsed_ms: number
  error: string | null
}

export interface FeishuPushResult {
  report_id: string
  title: string
  filename: string
  row_count: number
  chat_id: string
  mode: 'mock' | 'real'
  degraded: boolean
}

interface Envelope<T> {
  code: number
  message: string
  data: T
  trace_id: string
}

export interface ChatOptions {
  forceReport?: boolean
}

export function chatWithAssistant(
  question: string,
  options: ChatOptions = {},
): Promise<AssistantResult> {
  const payload: { question: string; force_report?: boolean } = { question }
  if (options.forceReport) payload.force_report = true
  return request<AssistantResult>('/api/assistant/chat', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function fetchAssistantMeta(): Promise<AssistantMeta> {
  return request<AssistantMeta>('/api/assistant/meta')
}

export function pushReportToFeishu(
  reportId: string,
  chatId?: string,
): Promise<FeishuPushResult> {
  return request<FeishuPushResult>('/api/feishu/push/report', {
    method: 'POST',
    body: JSON.stringify({ report_id: reportId, chat_id: chatId }),
  })
}

export async function transcribeAudio(
  audio: Blob,
  language = 'zh',
): Promise<AsrResult> {
  const form = new FormData()
  form.append('audio', audio, 'voice.wav')
  form.append('language', language)

  let response: Response
  try {
    response = await fetch(`${API_BASE}/api/asr/transcribe`, {
      method: 'POST',
      body: form,
    })
  } catch {
    throw new ApiError(1000, '无法连接后端服务，请确认 8001 端口已启动')
  }

  let payload: Envelope<AsrResult> | null = null
  try {
    payload = (await response.json()) as Envelope<AsrResult>
  } catch {
    payload = null
  }
  if (!payload) {
    throw new ApiError(1000, `语音服务返回异常（HTTP ${response.status}）`, response.status)
  }
  if (!response.ok || payload.code !== 0) {
    throw new ApiError(payload.code, payload.message || '语音转写失败', response.status)
  }
  return payload.data
}

export async function downloadReport(
  downloadUrl: string,
): Promise<{ blob: Blob; filename: string }> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}${downloadUrl}`)
  } catch {
    throw new ApiError(1000, '无法连接后端服务，请确认 8001 端口已启动')
  }
  if (!response.ok) {
    let message = `报表下载失败（HTTP ${response.status}）`
    try {
      const body = (await response.json()) as { message?: string }
      if (body.message) message = body.message
    } catch {
      // 二进制错误响应没有 JSON 体时使用默认提示。
    }
    throw new ApiError(1000, message, response.status)
  }
  const disposition = response.headers.get('content-disposition') ?? ''
  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i)
  const plainMatch = disposition.match(/filename="([^"]+)"/i)
  const filename = utf8Match
    ? decodeURIComponent(utf8Match[1])
    : plainMatch?.[1] || '校园报表.xlsx'
  return { blob: await response.blob(), filename }
}
