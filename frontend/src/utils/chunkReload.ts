const RELOAD_AT_KEY = 'pyrit:chunk-reload-at'
const RELOAD_WINDOW_MS = 10_000

/**
 * Reload the page once when a lazily loaded screen fails to download.
 *
 * Screens are separate chunks with hashed file names. After a redeploy, an open tab still asks for
 * the old names, and the backend answers unknown paths with index.html, so the import fails. A
 * reload picks up the new index.html. If the reload happened within the last few seconds the chunk
 * is broken for another reason, so the error is left to the error boundary instead of looping.
 */
export function installChunkReloadHandler(
  target: Window = window,
  now: () => number = Date.now,
): () => void {
  const onPreloadError = (event: Event) => {
    try {
      const lastReload = Number(target.sessionStorage.getItem(RELOAD_AT_KEY) ?? 0)
      if (now() - lastReload < RELOAD_WINDOW_MS) return
      target.sessionStorage.setItem(RELOAD_AT_KEY, String(now()))
    } catch {
      return // without storage a reload loop can't be ruled out
    }
    event.preventDefault()
    target.location.reload()
  }
  target.addEventListener('vite:preloadError', onPreloadError)
  return () => target.removeEventListener('vite:preloadError', onPreloadError)
}
