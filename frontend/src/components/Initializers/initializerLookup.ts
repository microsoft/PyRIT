import type { RegisteredInitializer } from '@/types'

/**
 * Lifecycle of the registered-initializer catalog request.
 *
 * `loading` — the initial fetch has not settled yet.
 * `loaded` — the catalog is the current source of truth; lookups that miss
 *   genuinely mean "no longer registered".
 * `error` — the catalog fetch (or latest refresh) failed; entries may be
 *   stale, so catalog-derived metadata must not be rendered and controls
 *   that depend on the catalog stay disabled.
 */
export type CatalogStatus = 'loading' | 'loaded' | 'error'

/**
 * Resolve a settings entry's `initializer_name` to its catalog definition.
 *
 * Returns `undefined` when the name is not in the catalog; callers branch on
 * the catalog status to decide whether that means "no longer registered"
 * (`loaded`) or "metadata unavailable" (`error`/`loading`) instead of
 * rendering synthetic placeholder entries.
 */
export function findRegisteredInitializer(
  initializerName: string,
  registeredInitializers: RegisteredInitializer[],
): RegisteredInitializer | undefined {
  return registeredInitializers.find((item) => item.initializer_name === initializerName)
}

/**
 * Description shown for a row whose initializer has no catalog entry.
 *
 * Only a settled, successful catalog load can claim "no longer registered";
 * any other status means the metadata is simply not known yet.
 */
export function initializerFallbackDescription(catalogStatus: CatalogStatus): string {
  return catalogStatus === 'loaded'
    ? 'Initializer is no longer registered.'
    : 'Catalog metadata temporarily unavailable.'
}
