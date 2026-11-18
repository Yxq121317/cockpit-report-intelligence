import { request } from './http'

export interface EvalQueriesMetrics {
  total: number
  completed: number
  task_completion_rate: number
  faithfulness_avg: number | null
}

export interface EvalAssistantsMetrics {
  total: number
  completed: number
  task_completion_rate: number
  tool_calls: number
  tool_call_success: number
  tool_call_accuracy: number | null
  faithfulness_avg: number | null
}

export interface EvalFailuresMetrics {
  total: number
  by_category: Record<string, number>
}

export interface EvalMetrics {
  queries: EvalQueriesMetrics
  assistants: EvalAssistantsMetrics
  failures: EvalFailuresMetrics
  since: string | null
  updated_at: string | null
}

export interface MetricDefinition {
  key: string
  label: string
  description: string
}

export interface LangfuseStatus {
  enabled: boolean
  host: string
  environment: string
  release: string
  reason: string
  queued: number
}

export interface EvalSummary {
  metrics: EvalMetrics
  metric_definitions: MetricDefinition[]
  langfuse: LangfuseStatus
  failure_log_enabled: boolean
}

export interface EvalFailureScores {
  task_completion: number
  tool_call_accuracy: number | null
  faithfulness: number
}

export interface EvalFailure {
  id: string
  at: string
  scope: 'query' | 'assistant'
  category: string
  detail: string
  question: string
  intent: string | null
  sql: string | null
  answer: string
  scores: EvalFailureScores
}

export interface EvalFailures {
  count: number
  limit: number
  items: EvalFailure[]
}

export interface GoldenSummary {
  total: number
  passed: number
  pass_rate: number
  intent_accuracy: number
  task_completion_rate: number
  tool_call_accuracy: number | null
  faithfulness_avg: number
  schema_recall_avg: number | null
}

export type GoldenKind = 'query' | 'entry' | 'report' | 'chitchat'

export interface GoldenCase {
  id: string
  question: string
  kind: GoldenKind
  mode: 'mock' | 'real'
  expected_intent: string
  actual_intent: string
  intent_ok: boolean
  task_completion: number
  tool_call_accuracy: number | null
  faithfulness: number
  ungrounded: string[]
  schema_recall: number | null
  retrieved_tables: string[]
  passed: boolean
  failure: string | null
  row_count: number | null
}

export interface GoldenResult {
  available: boolean
  path?: string
  generated_at?: string
  mode?: 'mock' | 'real'
  summary: GoldenSummary | null
  cases: GoldenCase[]
  failures: GoldenCase[]
  output_path?: string
}

export function fetchEvalSummary(): Promise<EvalSummary> {
  return request<EvalSummary>('/api/eval/summary')
}

export function fetchEvalFailures(limit = 50): Promise<EvalFailures> {
  return request<EvalFailures>(`/api/eval/failures?limit=${limit}`)
}

export function fetchGoldenResult(): Promise<GoldenResult> {
  return request<GoldenResult>('/api/eval/golden')
}

/** 在应用内重新运行黄金评测；看板不触发 Langfuse 上报（push=false）。 */
export function runGoldenEvaluation(): Promise<GoldenResult> {
  return request<GoldenResult>('/api/eval/golden/run', { method: 'POST' })
}
