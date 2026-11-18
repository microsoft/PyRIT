import type { AxiosError } from 'axios'

import type { Operation } from '@/types'

export function conflictingOperation(cause: unknown): Operation | null {
  if (!isAxiosError(cause) || cause.response?.status !== 409) return null
  const data: unknown = cause.response.data
  if (typeof data !== 'object' || data === null || !('detail' in data)) return null
  const detail = data.detail
  if (typeof detail !== 'object' || detail === null || !('operation' in detail)) return null
  const operation = detail.operation
  if (typeof operation !== 'object' || operation === null
    || !('id' in operation) || typeof operation.id !== 'string'
    || !('name' in operation) || typeof operation.name !== 'string'
    || !('created_at' in operation) || typeof operation.created_at !== 'string') return null
  return { id: operation.id, name: operation.name, created_at: operation.created_at }
}

export interface FieldError {
  /** Location of the invalid input, e.g. `body.attack_result_id`. */
  field: string
  message: string
  code?: string
}

/**
 * Normalized error from any API call.
 *
 * Callers get a consistent shape regardless of whether the failure was a
 * network timeout, an Axios HTTP error with RFC 7807 JSON body, a proxy
 * returning HTML, or a non-Axios throw.
 */
export interface ApiError {
  /** HTTP status code, or null when the request never reached the server. */
  status: number | null
  /** Human-readable error message extracted from the response or synthesized. */
  detail: string
  /** RFC 7807 `type` URI, if the backend included one. */
  type?: string
  /** Per-field validation errors from the RFC 7807 `errors` array; empty when the body has none. */
  fieldErrors: FieldError[]
  /** True when no HTTP response was received (DNS failure, CORS block, etc.). */
  isNetworkError: boolean
  /** True when the request exceeded the configured timeout. */
  isTimeout: boolean
  /** The original thrown value, for advanced callers or logging. */
  raw: unknown
}

/**
 * Convert any caught value into a normalized {@link ApiError}.
 *
 * Handles:
 * - Axios errors with an RFC 7807 JSON body (`response.data.detail`)
 * - Axios errors with a plain-text body
 * - Axios errors with an HTML error page (e.g. a proxy 502/503), identified by
 *   `Content-Type` (or by content when the type is missing or generic), which
 *   falls back to a status message instead of showing markup
 * - Axios errors with no response at all (network / CORS)
 * - Axios timeout errors (`code === 'ECONNABORTED'`)
 * - Plain `Error` instances
 * - String throws
 * - Anything else (`unknown`)
 */
export function toApiError(err: unknown): ApiError {
  // Axios errors carry an `isAxiosError` flag.
  if (isAxiosError(err)) {
    // Timeout
    if (err.code === 'ECONNABORTED') {
      return {
        status: null,
        detail: 'Request timed out. The server may be busy — please try again.',
        fieldErrors: [],
        isNetworkError: false,
        isTimeout: true,
        raw: err,
      }
    }

    // No response at all (network error, DNS, CORS, etc.)
    if (!err.response) {
      return {
        status: null,
        detail: 'Network error — check that the backend is running and reachable.',
        fieldErrors: [],
        isNetworkError: true,
        isTimeout: false,
        raw: err,
      }
    }

    // We have an HTTP response — try to extract RFC 7807 detail
    const { status, data, headers } = err.response
    const { detail, type, fieldErrors } = extractDetail(data, responseMediaType(headers))

    return {
      status,
      detail: detail || `Server error (${status})`,
      type,
      fieldErrors,
      isNetworkError: false,
      isTimeout: false,
      raw: err,
    }
  }

  // Plain Error
  if (err instanceof Error) {
    return {
      status: null,
      detail: err.message || 'An unexpected error occurred.',
      fieldErrors: [],
      isNetworkError: false,
      isTimeout: false,
      raw: err,
    }
  }

  // String throw
  if (typeof err === 'string') {
    return {
      status: null,
      detail: err,
      fieldErrors: [],
      isNetworkError: false,
      isTimeout: false,
      raw: err,
    }
  }

  // Unknown throw (null, undefined, number, object, etc.)
  return {
    status: null,
    detail: 'An unexpected error occurred.',
    fieldErrors: [],
    isNetworkError: false,
    isTimeout: false,
    raw: err,
  }
}

/** Respect a server-directed cooldown before a user retries a busy read. */
export function retryAfterMilliseconds(error: unknown): number {
  if (!isAxiosError(error) || error.response?.status !== 503) return 0
  const header: unknown = error.response.headers?.['retry-after']
  if (typeof header !== 'string' && typeof header !== 'number') return 0
  const seconds = Number(header)
  if (Number.isFinite(seconds) && seconds >= 0) return seconds * 1000
  const date = typeof header === 'string' ? Date.parse(header) : NaN
  return Number.isFinite(date) ? Math.max(0, date - Date.now()) : 0
}

/**
 * Replace a file download's Blob error body with its JSON, or its text when it
 * isn't JSON, so compatibility checks, token refresh and {@link toApiError} can
 * read it. An unreadable body is left as is; other errors are unchanged.
 */
export async function decodeBlobErrorAsync(error: unknown): Promise<void> {
  const response = isAxiosError(error) ? error.response : undefined
  const body: unknown = response?.data
  if (!response || !(body instanceof Blob)) return
  let text: string
  try {
    text = await readBlobText(body)
  } catch {
    return
  }
  try {
    response.data = JSON.parse(text)
  } catch {
    response.data = text
  }
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

const HTML_MEDIA_TYPES = new Set(['text/html', 'application/xhtml+xml'])
/** Media types that say nothing about the body, so its content decides. */
const UNSPECIFIED_MEDIA_TYPES = new Set(['', 'application/octet-stream'])
const HTML_DOCUMENT_PATTERN = /^\s*(?:<\?xml[^>]*\?>\s*)?(?:<!--[\s\S]*?-->\s*)*<(?:!doctype\s+html|html|head|body)\b/i

/** Type-guard for Axios errors. */
function isAxiosError(err: unknown): err is AxiosError {
  return typeof err === 'object' && err !== null && (err as AxiosError).isAxiosError === true
}

function readBlobText(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = () => reject(reader.error)
    reader.readAsText(blob)
  })
}

/**
 * Extract `detail` and `type` from an Axios response body.
 *
 * The body may be:
 * - An RFC 7807 JSON object with `.detail` and optionally `.type`
 * - A plain-text string
 * - An HTML error page from a proxy or host, which yields no detail
 * - Something else entirely (null, number, etc.)
 */
function extractDetail(data: unknown, mediaType: string): {
  detail: string | undefined; type: string | undefined; fieldErrors: FieldError[]
} {
  if (typeof data === 'string') {
    const detail = isHtmlBody(data, mediaType) ? undefined : data
    return { detail, type: undefined, fieldErrors: [] }
  }
  if (typeof data === 'object' && data !== null) {
    const obj = data as Record<string, unknown>
    const detail = typeof obj.detail === 'string' ? obj.detail : undefined
    const type = typeof obj.type === 'string' ? obj.type : undefined
    return { detail, type, fieldErrors: extractFieldErrors(obj.errors) }
  }
  return { detail: undefined, type: undefined, fieldErrors: [] }
}

/** Lowercase media type from the response `Content-Type`, without parameters; empty when absent. */
function responseMediaType(headers: unknown): string {
  if (typeof headers !== 'object' || headers === null) return ''
  const header = Object.entries(headers).find(([name]) => name.toLowerCase() === 'content-type')
  const value: unknown = header?.[1]
  return typeof value === 'string' ? (value.split(';')[0] ?? '').trim().toLowerCase() : ''
}

/** Trust an explicit media type; inspect the body only when the type is missing or generic. */
function isHtmlBody(body: string, mediaType: string): boolean {
  if (HTML_MEDIA_TYPES.has(mediaType)) return true
  return UNSPECIFIED_MEDIA_TYPES.has(mediaType) && HTML_DOCUMENT_PATTERN.test(body)
}

function extractFieldErrors(errors: unknown): FieldError[] {
  if (!Array.isArray(errors)) return []
  const result: FieldError[] = []
  for (const item of errors) {
    if (typeof item !== 'object' || item === null) continue
    const { field, message, code } = item as Record<string, unknown>
    if (typeof field !== 'string' || typeof message !== 'string') continue
    result.push(typeof code === 'string' ? { field, message, code } : { field, message })
  }
  return result
}
