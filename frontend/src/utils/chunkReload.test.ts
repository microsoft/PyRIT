import { installChunkReloadHandler, isChunkLoadError } from './chunkReload'

function fakeWindow(entryScript = '/assets/index-aaa.js') {
  const target = new EventTarget() as EventTarget & {
    sessionStorage: Storage
    location: { reload: jest.Mock }
    document: { querySelector: () => { getAttribute: () => string } }
  }
  target.document = { querySelector: () => ({ getAttribute: () => entryScript }) }
  const store = new Map<string, string>()
  target.sessionStorage = {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => { store.set(key, value) },
  } as Storage
  target.location = { reload: jest.fn() }
  return target
}

function preloadError(target: EventTarget, chunk = '/assets/AnalyticsPage-abc123.js') {
  const event = new Event('vite:preloadError', { cancelable: true }) as Event & { payload: Error }
  event.payload = new TypeError(`Failed to fetch dynamically imported module: http://localhost${chunk}`)
  target.dispatchEvent(event)
  return event
}

describe('installChunkReloadHandler', () => {
  it('reloads once when a screen chunk fails to load', () => {
    const target = fakeWindow()
    installChunkReloadHandler(target as unknown as Window)

    const event = preloadError(target)

    expect(target.location.reload).toHaveBeenCalledTimes(1)
    expect(event.defaultPrevented).toBe(true)
  })

  it('leaves a chunk that still fails after its reload to the error boundary', () => {
    const target = fakeWindow()
    installChunkReloadHandler(target as unknown as Window)
    preloadError(target)

    const event = preloadError(target)

    expect(target.location.reload).toHaveBeenCalledTimes(1)
    expect(event.defaultPrevented).toBe(false)
  })

  it('reloads once for a different chunk, as after another redeploy', () => {
    const target = fakeWindow()
    installChunkReloadHandler(target as unknown as Window)
    preloadError(target, '/assets/AnalyticsPage-abc123.js')

    preloadError(target, '/assets/AnalyticsPage-def456.js')

    expect(target.location.reload).toHaveBeenCalledTimes(2)
  })

  it('reloads once per build for an error without a chunk URL', () => {
    const target = fakeWindow('/assets/index-aaa.js')
    installChunkReloadHandler(target as unknown as Window)
    const urlless = () => {
      const event = new Event('vite:preloadError', { cancelable: true }) as Event & { payload: Error }
      event.payload = new TypeError('Importing a module script failed.')
      target.dispatchEvent(event)
    }
    urlless()
    urlless()
    expect(target.location.reload).toHaveBeenCalledTimes(1)

    target.document = { querySelector: () => ({ getAttribute: () => '/assets/index-bbb.js' }) }
    urlless()
    expect(target.location.reload).toHaveBeenCalledTimes(2)
  })

  it('does not reload when session storage is unavailable', () => {
    const target = fakeWindow()
    target.sessionStorage.getItem = () => { throw new Error('denied') }
    installChunkReloadHandler(target as unknown as Window)

    const event = preloadError(target)

    expect(target.location.reload).not.toHaveBeenCalled()
    expect(event.defaultPrevented).toBe(false)
  })

  it('stops listening when uninstalled', () => {
    const target = fakeWindow()
    const uninstall = installChunkReloadHandler(target as unknown as Window)

    uninstall()
    preloadError(target)

    expect(target.location.reload).not.toHaveBeenCalled()
  })
})

describe('isChunkLoadError', () => {
  it.each([
    'Failed to fetch dynamically imported module: http://x/assets/A.js',
    'Importing a module script failed.',
    'error loading dynamically imported module: http://x/assets/A.js',
    'Unable to preload CSS for /assets/A.css',
  ])('recognizes %s', (message) => {
    expect(isChunkLoadError(new TypeError(message))).toBe(true)
  })

  it('ignores other errors', () => {
    expect(isChunkLoadError(new Error('Cannot read properties of undefined'))).toBe(false)
    expect(isChunkLoadError('Failed to fetch dynamically imported module')).toBe(false)
  })
})
