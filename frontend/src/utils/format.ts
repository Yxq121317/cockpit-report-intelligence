export function formatNumber(value?: number | null, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '--'
  return value.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

export function formatCell(
  value: string | number | boolean | null | undefined,
  kind?: 'string' | 'number',
): string {
  if (value === null || value === undefined || value === '') return '--'
  if (kind === 'number' && typeof value === 'number') {
    return Number.isInteger(value) ? formatNumber(value) : formatNumber(value, 1)
  }
  if (typeof value === 'boolean') return value ? '是' : '否'
  return String(value)
}
