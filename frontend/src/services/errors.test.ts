import { decodeBlobErrorAsync, retryAfterMilliseconds, toApiError } from './errors'
import type { ApiError } from './errors'

// ---------------------------------------------------------------------------
// Helper: build a fake AxiosError-like object
// ---------------------------------------------------------------------------
function makeAxiosError(opts: {
  status?: number
  data?: unknown
  code?: string
  message?: string
  hasResponse?: boolean
}): unknown {
  const { status, data, code, message = 'Request failed', hasResponse = true } = opts

  const err: Record<string, unknown> = {
    isAxiosError: true,
    message,
    code,
    config: {},
    toJSON: () => ({}),
  }

  if (hasResponse && status !== undefined) {
    err.response = { status, data, headers: {}, config: {} }
  }

  return err
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('retryAfterMilliseconds', () => {
  it('should honor the busy response cooldown and HTTP-date form', () => {
    jest.useFakeTimers().setSystemTime(new Date('2026-01-01T00:00:00Z'))
    try {
      const error = { isAxiosError: true, response: { status: 503, headers: { 'retry-after': '1' } } }
      expect(retryAfterMilliseconds(error)).toBe(1000)
      expect(retryAfterMilliseconds({ ...error, response: { status: 503, headers: {
        'retry-after': 'Thu, 01 Jan 2026 00:00:02 GMT',
      } } })).toBe(2000)
      expect(retryAfterMilliseconds(new Error('Unavailable'))).toBe(0)
      expect(retryAfterMilliseconds({ ...error, response: { status: 504, headers: { 'retry-after': '1' } } })).toBe(0)
      expect(retryAfterMilliseconds({ ...error, response: { status: 503, headers: { 'retry-after': 'invalid' } } })).toBe(0)
    } finally {
      jest.useRealTimers()
    }
  })
})

describe('toApiError', () => {
  // 1. Axios error with RFC 7807 JSON body
  it('extracts detail and type from RFC 7807 JSON body', () => {
    const err = makeAxiosError({
      status: 400,
      data: {
        type: 'https://tools.ietf.org/html/rfc7807',
        title: 'Bad Request',
        status: 400,
        detail: 'Conversation ID is required.',
      },
    })

    const result: ApiError = toApiError(err)

    expect(result.status).toBe(400)
    expect(result.detail).toBe('Conversation ID is required.')
    expect(result.type).toBe('https://tools.ietf.org/html/rfc7807')
    expect(result.isNetworkError).toBe(false)
    expect(result.isTimeout).toBe(false)
    expect(result.raw).toBe(err)
  })

  // 2. Axios error with plain-string body (e.g. proxy HTML)
  it('uses a plain-string response body as detail', () => {
    const err = makeAxiosError({
      status: 502,
      data: '<html><body>Bad Gateway</body></html>',
    })

    const result = toApiError(err)

    expect(result.status).toBe(502)
    expect(result.detail).toBe('<html><body>Bad Gateway</body></html>')
    expect(result.type).toBeUndefined()
    expect(result.isNetworkError).toBe(false)
  })

  // 3. Axios error with HTML body (nginx 502 — object without detail)
  it('falls back to status text when JSON body lacks detail', () => {
    const err = makeAxiosError({
      status: 500,
      data: { error: 'internal' }, // no 'detail' field
    })

    const result = toApiError(err)

    expect(result.status).toBe(500)
    expect(result.detail).toBe('Server error (500)')
    expect(result.type).toBeUndefined()
  })

  // 4. Axios error with no response (network / CORS)
  it('returns isNetworkError for Axios errors without response', () => {
    const err = makeAxiosError({
      hasResponse: false,
      message: 'Network Error',
    })

    const result = toApiError(err)

    expect(result.status).toBeNull()
    expect(result.detail).toBe('Network error — check that the backend is running and reachable.')
    expect(result.isNetworkError).toBe(true)
    expect(result.isTimeout).toBe(false)
  })

  // 5. Axios timeout error
  it('returns isTimeout for ECONNABORTED', () => {
    const err = makeAxiosError({
      hasResponse: false,
      code: 'ECONNABORTED',
      message: 'timeout of 5000ms exceeded',
    })

    const result = toApiError(err)

    expect(result.status).toBeNull()
    expect(result.detail).toContain('timed out')
    expect(result.isTimeout).toBe(true)
    expect(result.isNetworkError).toBe(false)
  })

  // 6. Non-Axios Error instance
  it('uses Error.message for non-Axios Error instances', () => {
    const err = new Error('Something broke')

    const result = toApiError(err)

    expect(result.status).toBeNull()
    expect(result.detail).toBe('Something broke')
    expect(result.isNetworkError).toBe(false)
    expect(result.isTimeout).toBe(false)
    expect(result.raw).toBe(err)
  })

  // 7. String throw
  it('uses the string directly for string throws', () => {
    const result = toApiError('unexpected failure')

    expect(result.status).toBeNull()
    expect(result.detail).toBe('unexpected failure')
    expect(result.isNetworkError).toBe(false)
  })

  // 8. Unknown throw (null)
  it('returns generic message for null throw', () => {
    const result = toApiError(null)

    expect(result.detail).toBe('An unexpected error occurred.')
    expect(result.raw).toBeNull()
  })

  // 9. Unknown throw (undefined)
  it('returns generic message for undefined throw', () => {
    const result = toApiError(undefined)

    expect(result.detail).toBe('An unexpected error occurred.')
    expect(result.raw).toBeUndefined()
  })

  // 10. Axios error with null data
  it('falls back to status text when response data is null', () => {
    const err = makeAxiosError({ status: 403, data: null })

    const result = toApiError(err)

    expect(result.status).toBe(403)
    expect(result.detail).toBe('Server error (403)')
  })

  // 11. Error with empty message
  it('falls back to generic message for Error with empty message', () => {
    const err = new Error('')

    const result = toApiError(err)

    expect(result.detail).toBe('An unexpected error occurred.')
  })

  // 12. Axios 422 with RFC 7807 (validation error)
  it('handles 422 validation errors with detail field', () => {
    const err = makeAxiosError({
      status: 422,
      data: {
        type: 'validation_error',
        detail: 'Field "name" is required.',
        errors: [{ field: 'name', message: 'required' }],
      },
    })

    const result = toApiError(err)

    expect(result.status).toBe(422)
    expect(result.detail).toBe('Field "name" is required.')
    expect(result.type).toBe('validation_error')
  })
})

describe('decodeBlobErrorAsync', () => {
  it('replaces a JSON Blob body with the parsed problem and keeps the status', async () => {
    const problem = { detail: 'Operation not found' }
    const err = makeAxiosError({
      status: 404,
      data: new Blob([JSON.stringify(problem)], { type: 'application/json' }),
    })

    await decodeBlobErrorAsync(err)

    expect(err).toMatchObject({ response: { status: 404, data: problem } })
    expect(toApiError(err)).toMatchObject({ status: 404, detail: 'Operation not found' })
  })

  it('keeps a non-JSON Blob body, such as a proxy page, as readable text', async () => {
    const page = '<html><body>Bad Gateway</body></html>'
    const err = makeAxiosError({ status: 502, data: new Blob([page], { type: 'text/html' }) })

    await decodeBlobErrorAsync(err)

    expect(err).toMatchObject({ response: { status: 502, data: page } })
    expect(toApiError(err)).toMatchObject({ status: 502, detail: page })
  })

  it('leaves the error failing with its status when the Blob cannot be read', async () => {
    const read = jest.spyOn(FileReader.prototype, 'readAsText').mockImplementation(() => undefined)
    try {
      const body = new Blob(['{"detail":"Unreadable"}'], { type: 'application/json' })
      const err = makeAxiosError({ status: 500, data: body })

      const decoding = decodeBlobErrorAsync(err)
      const reader = read.mock.contexts[0]
      if (!(reader instanceof FileReader)) throw new Error('Blob read did not start')
      reader.dispatchEvent(new ProgressEvent('error'))

      await expect(decoding).resolves.toBeUndefined()
      expect(err).toMatchObject({ response: { status: 500 } })
      expect((err as { response: { data: unknown } }).response.data).toBe(body)
      expect(toApiError(err)).toMatchObject({ status: 500, detail: 'Server error (500)' })
    } finally {
      read.mockRestore()
    }
  })

  it.each([
    ['a JSON body', { detail: 'Finding not found' }],
    ['a text body', '<html>Bad Gateway</html>'],
    ['no body', null],
  ])('leaves an Axios error with %s unchanged', async (_name, data) => {
    const err = makeAxiosError({ status: 404, data })

    await decodeBlobErrorAsync(err)

    expect((err as { response: { data: unknown } }).response.data).toBe(data)
  })

  it('ignores a Blob that is not an Axios response body', async () => {
    const body = new Blob(['{"detail":"Not from Axios"}'], { type: 'application/json' })
    const notAxios = { response: { status: 404, data: body } }

    await decodeBlobErrorAsync(notAxios)
    await decodeBlobErrorAsync(new Error('Something broke'))
    await decodeBlobErrorAsync(makeAxiosError({ hasResponse: false }))

    expect(notAxios.response.data).toBe(body)
  })
})
