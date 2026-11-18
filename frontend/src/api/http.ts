export const API_BASE = (import.meta.env.VITE_API_BASE || 'http://localhost:8001').replace(/\/$/, '')

interface Envelope<T> {
  code: number
  message: string
  data: T
  trace_id: string
}

export class ApiError extends Error {
  readonly code: number
  readonly status: number

  constructor(code: number, message: string, status = 0) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
  }
}

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  if (options.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, { ...options, headers })
  } catch {
    throw new ApiError(1000, '无法连接后端服务，请确认 8001 端口已启动')
  }

  let payload: Envelope<T> | null = null
  try {
    payload = (await response.json()) as Envelope<T>
  } catch {
    payload = null
  }

  if (!payload) {
    throw new ApiError(1000, `服务返回异常（HTTP ${response.status}）`, response.status)
  }

  if (!response.ok || payload.code !== 0) {
    throw new ApiError(payload.code, payload.message || '请求失败', response.status)
  }

  return payload.data
}
