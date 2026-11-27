import type { ScenarioConfigFormState } from '@/components/Scenarios/scenarioConfigForm'
import type { ScenarioDatasetRequest } from '@/types'

/** The editor's state, reduced to what an edit can change. */
export interface PresetDraft {
  name: string
  description: string
  showInScanner: boolean
  scenarioName: string
  config: ScenarioConfigFormState | null
}

/**
 * One string per draft meaning, so the leave guard can ask whether two drafts differ.
 *
 * The draft a preset loads as and the draft the controls write can describe one selection
 * in different shapes. The API returns every field of a stored preset with each unset one
 * spelled out, the controls write only what the operator touched and in the order it was
 * touched, and the selectors append in click order. So the comparison ignores key order,
 * compares techniques, datasets and filter values as selections, and reads an unset field
 * as unset however it is spelled. A value is never folded away: a pinned dataset, a limit,
 * a loading policy or a filter always reads as an edit.
 */
export function presetDraftSignature(draft: PresetDraft): string {
  const { config } = draft
  return JSON.stringify(sortedKeys({
    ...draft,
    config: config && {
      ...config,
      techniques: [...config.techniques].sort(),
      datasets: canonicalDatasetRequest(config.datasets),
    },
  }))
}

/** The same value with the keys of every object in sorted order. A list keeps its order, because a list can be an ordered one. */
function sortedKeys(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(sortedKeys)
  }
  if (value !== null && typeof value === 'object') {
    const sorted: [string, unknown][] = Object.entries(value as Record<string, unknown>)
      .sort(([first], [second]) => first.localeCompare(second))
      .map(([key, nested]) => [key, sortedKeys(nested)])
    return Object.fromEntries(sorted)
  }
  return value
}

/** An unset limit: left out, `null`, or the `'default'` the API returns for one. */
function isUnsetLimit(value: unknown): boolean {
  return value === undefined || value === null || value === 'default'
}

/** An unset loading policy. The server reads an empty string as no policy as well. */
function isUnsetPolicy(value: unknown): boolean {
  return isUnsetLimit(value) || value === ''
}

/** The dataset request with nothing unset left in it, and with its selections in a fixed order. */
function canonicalDatasetRequest(request: ScenarioDatasetRequest): Record<string, unknown> {
  const { sources, max_per_dataset, max_total, fetch, filters, ...rest } = request
  const canonical: Record<string, unknown> = rest

  const canonicalSources = [...(sources ?? [])]
    .sort((first, second) => first.name.localeCompare(second.name))
    .map(({ max_size, fetch: policy, ...source }) => {
      const entry: Record<string, unknown> = { ...source }
      if (!isUnsetLimit(max_size)) entry.max_size = max_size
      if (!isUnsetPolicy(policy)) entry.fetch = policy
      return entry
    })
  if (canonicalSources.length > 0) canonical.sources = canonicalSources

  if (!isUnsetLimit(max_per_dataset)) canonical.max_per_dataset = max_per_dataset
  if (!isUnsetLimit(max_total)) canonical.max_total = max_total
  if (!isUnsetPolicy(fetch)) canonical.fetch = fetch

  const canonicalFilters: [string, string[]][] = Object.entries(filters ?? {})
    .map(([key, values]) => [key, [...values].sort()])
  if (canonicalFilters.length > 0) canonical.filters = Object.fromEntries(canonicalFilters)

  return canonical
}
