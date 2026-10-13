import type { DatasetInfo, ScenarioDatasetOptions, ScenarioDatasetSettings, ScenarioDatasetSourceSettings } from '@/types'

export function parseCaseLimit(text: string): number | null {
  const value = text.trim()
  const number = Number(value)
  return /^\d+$/.test(value) && Number.isSafeInteger(number) && number > 0 ? number : null
}

export function datasetSize(
  dataset: DatasetInfo | undefined, limit: ScenarioDatasetSettings['max_total'],
): string {
  const count = dataset?.loaded ? dataset.logical_examples : null
  if (limit === 'all') return count != null
    ? `${count.toLocaleString()} examples`
    : dataset?.loaded ? 'Loaded' : 'Not loaded'
  return count != null
    ? `Up to ${Math.min(limit, count).toLocaleString()} of ${count.toLocaleString()}`
    : `Up to ${limit.toLocaleString()}`
}

export function datasetChoices(
  datasets: DatasetInfo[], defaults: ScenarioDatasetSourceSettings[], options: ScenarioDatasetOptions,
): string[] {
  const names = new Set([...datasets.map((item) => item.name), ...defaults.map((item) => item.name)])
  return [...names].filter((name) => options.mode === 'any' || options.allowed_names.includes(name)).sort()
}

export function datasetFacts(datasets: DatasetInfo[], names: string[]) {
  const loaded = datasets.filter((item) => item.loaded && names.includes(item.name))
  return {
    loaded,
    allLoaded: names.length > 0 && loaded.length === names.length,
  }
}
