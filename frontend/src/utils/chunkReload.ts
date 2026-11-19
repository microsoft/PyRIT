const RELOADED_KEY = 'pyrit:chunk-reloaded'
const MAX_REMEMBERED = 50

// Browsers word a failed dynamic import differently; Vite adds its own CSS preload message.
const CHUNK_ERROR_PATTERN =
  /Failed to fetch dynamically imported module|Importing a module script failed|error loading dynamically imported module|Unable to preload CSS/i

/** Whether an error comes from a lazily loaded chunk that failed to download. */
export function isChunkLoadError(error: unknown): boolean {
  return error instanceof Error && CHUNK_ERROR_PATTERN.test(error.message)
}

function failedChunk(payload: unknown): string {
  const message = payload instanceof Error ? payload.message : String(payload)
  return message.match(/\/assets\/[^\s'")]+/)?.[0] ?? message
}

/**
 * Reload the page once when a lazily loaded screen fails to download.
 *
 * Screens are separate chunks with hashed file names. After a redeploy, an open tab still asks for
 * the old names, and the backend answers unknown paths with index.html, so the import fails. A
 * reload picks up the new index.html. Each failed chunk gets one reload per tab: a chunk that
 * still fails after the reload is broken for another reason, so the error is left to the error
 * boundary instead of looping.
 */
export function installChunkReloadHandler(target: Window = window): () => void {
  const onPreloadError = (event: Event) => {
    const chunk = failedChunk((event as Event & { payload?: unknown }).payload)
    try {
      const reloaded: string[] = JSON.parse(target.sessionStorage.getItem(RELOADED_KEY) ?? '[]')
      if (reloaded.includes(chunk)) return
      target.sessionStorage.setItem(
        RELOADED_KEY,
        JSON.stringify([...reloaded, chunk].slice(-MAX_REMEMBERED)),
      )
    } catch {
      return // without storage a reload loop can't be ruled out
    }
    event.preventDefault()
    target.location.reload()
  }
  target.addEventListener('vite:preloadError', onPreloadError)
  return () => target.removeEventListener('vite:preloadError', onPreloadError)
}
