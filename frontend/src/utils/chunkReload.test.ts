import { installChunkReloadHandler } from './chunkReload'

function fakeWindow() {
  const target = new EventTarget() as EventTarget & {
    sessionStorage: Storage
    location: { reload: jest.Mock }
  }
  const store = new Map<string, string>()
  target.sessionStorage = {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => { store.set(key, value) },
  } as Storage
  target.location = { reload: jest.fn() }
  return target
}

function preloadError(target: EventTarget) {
  const event = new Event('vite:preloadError', { cancelable: true })
  target.dispatchEvent(event)
  return event
}

describe('installChunkReloadHandler', () => {
  it('reloads once when a screen chunk fails to load', () => {
    const target = fakeWindow()
    installChunkReloadHandler(target as unknown as Window, () => 100_000)

    const event = preloadError(target)

    expect(target.location.reload).toHaveBeenCalledTimes(1)
    expect(event.defaultPrevented).toBe(true)
  })

  it('leaves a second failure right after a reload to the error boundary', () => {
    const target = fakeWindow()
    let clock = 100_000
    installChunkReloadHandler(target as unknown as Window, () => clock)
    preloadError(target)

    clock += 2_000
    const event = preloadError(target)

    expect(target.location.reload).toHaveBeenCalledTimes(1)
    expect(event.defaultPrevented).toBe(false)
  })

  it('reloads again once the window has passed', () => {
    const target = fakeWindow()
    let clock = 100_000
    installChunkReloadHandler(target as unknown as Window, () => clock)
    preloadError(target)

    clock += 60_000
    preloadError(target)

    expect(target.location.reload).toHaveBeenCalledTimes(2)
  })

  it('does not reload when session storage is unavailable', () => {
    const target = fakeWindow()
    target.sessionStorage.getItem = () => { throw new Error('denied') }
    installChunkReloadHandler(target as unknown as Window, () => 100_000)

    const event = preloadError(target)

    expect(target.location.reload).not.toHaveBeenCalled()
    expect(event.defaultPrevented).toBe(false)
  })

  it('stops listening when uninstalled', () => {
    const target = fakeWindow()
    const uninstall = installChunkReloadHandler(target as unknown as Window, () => 100_000)

    uninstall()
    preloadError(target)

    expect(target.location.reload).not.toHaveBeenCalled()
  })
})
