class RouterTestRequest {
  readonly url: string
  readonly method: string
  readonly signal: AbortSignal

  constructor(input: string | URL, init?: { method?: string; signal?: AbortSignal | null }) {
    this.url = String(input)
    this.method = init?.method ?? 'GET'
    this.signal = init?.signal ?? new AbortController().signal
  }
}

/** Provides the Request fields used by data-router navigation in JSDOM. */
export function installRouterTestRequest(): () => void {
  const original = Object.getOwnPropertyDescriptor(globalThis, 'Request')
  Object.defineProperty(globalThis, 'Request', {
    configurable: true, writable: true, value: RouterTestRequest,
  })
  return () => {
    if (original) Object.defineProperty(globalThis, 'Request', original)
    else Reflect.deleteProperty(globalThis, 'Request')
  }
}
