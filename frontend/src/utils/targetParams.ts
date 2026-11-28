/** Format an unknown scalar or object value without triggering default `[object Object]` coercion. */
export function formatUnknownValue(value: unknown, indent?: number): string {
  switch (typeof value) {
    case 'string':
    case 'number':
    case 'boolean':
    case 'bigint':
      return String(value)
    case 'symbol':
      return value.description ? `Symbol(${value.description})` : 'Symbol()'
    case 'undefined':
      return ''
    case 'function':
      return '[function]'
    case 'object':
      if (value === null) return ''
      return JSON.stringify(value, null, indent) ?? ''
  }
}

/** Format `target_specific_params` into a newline-separated summary. */
export function formatTargetParams(params?: Record<string, unknown> | null, indent?: number): string {
  if (!params) return ''
  const lines: string[] = []
  for (const [key, val] of Object.entries(params)) {
    if (val == null) continue
    if (key === 'extra_body_parameters' && typeof val === 'object') {
      for (const [k, v] of Object.entries(val as Record<string, unknown>)) {
        lines.push(`${k}: ${formatUnknownValue(v, indent)}`)
      }
    } else {
      lines.push(`${key}: ${formatUnknownValue(val, indent)}`)
    }
  }
  return lines.join('\n')
}
