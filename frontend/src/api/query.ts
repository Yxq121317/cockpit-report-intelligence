import { API_BASE, ApiError, request } from './http'

export interface QueryColumn {
  key: string
  label: string
  type?: 'string' | 'number'
}

export type QueryRow = Record<string, string | number | boolean | null>

export interface QueryChart {
  type: 'bar' | 'line' | 'table'
  x: string | null
  y: string[]
  series: Array<{ name: string; key: string }>
}

export interface QueryResult {
  question: string
  title: string
  sql: string | null
  explanation: string
  columns: QueryColumn[]
  rows: QueryRow[]
  chart: QueryChart
  answer: string
  summary: string
  row_count: number
  elapsed_ms: number
  need_clarification: boolean
  degraded: boolean
  model_mode: 'mock' | 'real'
}

export interface MetaColumn {
  name: string
  label: string
}

export interface MetaTable {
  name: string
  label: string
  description: string
  columns: MetaColumn[]
}

export interface MetaInfo {
  database: string
  table_count: number
  max_rows: number
  llm_mode: 'mock' | 'real'
  llm_degraded: boolean
  tables: MetaTable[]
  examples: Array<{ question: string; category: string }>
}

export interface ExportPayload {
  format: 'xlsx' | 'csv'
  filename: string
  columns: Array<{ key: string; label: string }>
  rows: QueryRow[]
}

export function fetchMeta(): Promise<MetaInfo> {
  return request<MetaInfo>('/api/query/meta')
}

export function askQuestion(question: string): Promise<QueryResult> {
  return request<QueryResult>('/api/query/nl', {
    method: 'POST',
    body: JSON.stringify({ question }),
  })
}

export async function downloadExport(
  payload: ExportPayload,
): Promise<{ blob: Blob; filename: string }> {
  const response = await fetch(`${API_BASE}/api/query/export`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!response.ok) {
    let message = `导出失败（HTTP ${response.status}）`
    try {
      const body = (await response.json()) as { message?: string }
      if (body.message) message = body.message
    } catch {
      // 二进制错误响应没有 JSON 体时使用默认提示。
    }
    throw new ApiError(1000, message, response.status)
  }

  const disposition = response.headers.get('content-disposition') ?? ''
  const match = disposition.match(/filename\*=UTF-8''([^;]+)/)
  const filename = match
    ? decodeURIComponent(match[1])
    : `导出数据.${payload.format}`
  return { blob: await response.blob(), filename }
}
