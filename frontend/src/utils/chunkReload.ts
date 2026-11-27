// Browsers word a failed dynamic import differently; Vite adds its own CSS preload message.
const CHUNK_ERROR_PATTERN =
  /Failed to fetch dynamically imported module|Importing a module script failed|error loading dynamically imported module|Unable to preload CSS/i

/**
 * Whether an error comes from a lazily loaded chunk that failed to download.
 *
 * Screens are separate chunks with hashed file names. After a redeploy, an open tab still asks for
 * the old names, and the backend answers unknown paths with index.html, so the import fails. The
 * same happens while the server is unreachable. Only a reload fetches the chunk again, and a reload
 * discards unsaved work such as chat drafts, so the error boundary offers it instead of reloading
 * on its own.
 */
export function isChunkLoadError(error: unknown): boolean {
  return error instanceof Error && CHUNK_ERROR_PATTERN.test(error.message)
}
