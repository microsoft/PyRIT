const STORAGE_KEY = 'pyrit.globalLabels'

/**
 * The labels the user last chose, so a refresh does not quietly put the next
 * run back on the placeholder operation. Only string values are kept, so a
 * hand-edited or half-written entry cannot reach the rest of the app.
 */
export function readStoredGlobalLabels(): Record<string, string> {
  if (typeof window === 'undefined') return {}
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return {}
    const parsed: unknown = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {}
    return Object.fromEntries(
      Object.entries(parsed as Record<string, unknown>).filter(
        ([, value]) => typeof value === 'string',
      ),
    ) as Record<string, string>
  } catch {
    return {}
  }
}

export function persistGlobalLabels(labels: Record<string, string>): void {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(labels))
  } catch {
    /* localStorage may be unavailable (private mode, quota, sandboxed iframe). */
  }
}
