import type { DatasetInfo, RequestedDatasetsState } from '@/types'

/** Resolve linked names from the picker snapshot instead of making a second catalog request. */
export function resolveRequestedDatasets(
  names: readonly string[],
  catalog: { readonly items: DatasetInfo[]; readonly loading: boolean; readonly error: string | null },
): RequestedDatasetsState {
  if (names.length === 0) return { loading: false, known: [], unknown: [], error: null }
  if (catalog.loading) return { loading: true, known: [], unknown: [], error: null }
  if (catalog.error) return { loading: false, known: [], unknown: [...names], error: catalog.error }
  const available = new Set(catalog.items.map((item) => item.name))
  return {
    loading: false,
    known: names.filter((name) => available.has(name)),
    unknown: names.filter((name) => !available.has(name)),
    error: null,
  }
}
