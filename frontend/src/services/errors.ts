import type { AxiosError } from 'axios'

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
 * - Axios errors with a plain-string body (e.g. nginx 502 HTML)
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
        isNetworkError: true,
        isTimeout: false,
        raw: err,
      }
    }

    // We have an HTTP response — try to extract RFC 7807 detail
    const { status, data } = err.response
    const { detail, type } = extractDetail(data)

    return {
      status,
      detail: detail || `Server error (${status})`,
      type,
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
      isNetworkError: false,
      isTimeout: false,
      raw: err,
    }
  }

  // Unknown throw (null, undefined, number, object, etc.)
  return {
    status: null,
    detail: 'An unexpected error occurred.',
    isNetworkError: false,
    isTimeout: false,
    raw: err,
  }
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/** Type-guard for Axios errors. */
function isAxiosError(err: unknown): err is AxiosError {
  return typeof err === 'object' && err !== null && (err as AxiosError).isAxiosError === true
}

/**
 * Extract `detail` and `type` from an Axios response body.
 *
 * The body may be:
 * - An RFC 7807 JSON object with `.detail` and optionally `.type`
 * - A plain string (e.g. nginx HTML error page)
 * - Something else entirely (null, number, etc.)
 */
function extractDetail(data: unknown): { detail: string | undefined; type: string | undefined } {
  if (typeof data === 'string') {
    return { detail: data, type: undefined }
  }
  if (typeof data === 'object' && data !== null) {
    const obj = data as Record<string, unknown>
    const detail = typeof obj.detail === 'string' ? obj.detail : undefined
    const type = typeof obj.type === 'string' ? obj.type : undefined
    return { detail, type }
  }
  return { detail: undefined, type: undefined }
}
