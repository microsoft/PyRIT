import type {
  ScenarioRunEstimateDataset,
  ScenarioRunEstimateDatasetCap,
} from '@/types'

interface NormalizedDatasetCaps {
  readonly commonCaps: ScenarioRunEstimateDatasetCap[]
  readonly residualCapsByDatasetId: ReadonlyMap<string, ScenarioRunEstimateDatasetCap[]>
}

function semanticCapKey(cap: ScenarioRunEstimateDatasetCap): string {
  return JSON.stringify([cap.label, cap.count, cap.configuredOn])
}

function capOccurrences(caps: ScenarioRunEstimateDatasetCap[]): Map<string, number> {
  const occurrences = new Map<string, number>()
  for (const cap of caps) {
    const key = semanticCapKey(cap)
    occurrences.set(key, (occurrences.get(key) ?? 0) + 1)
  }
  return occurrences
}

export function normalizeDatasetCaps(datasets: ScenarioRunEstimateDataset[]): NormalizedDatasetCaps {
  const commonCaps: ScenarioRunEstimateDatasetCap[] = []
  const seenListLevelCaps = new Set<string>()

  for (const dataset of datasets) {
    for (const cap of dataset.configuredCaps) {
      if (cap.configuredOn !== 'compound') {
        continue
      }
      const key = semanticCapKey(cap)
      if (!seenListLevelCaps.has(key)) {
        seenListLevelCaps.add(key)
        commonCaps.push(cap)
      }
    }
  }

  const universalRowCapOccurrences = datasets.length > 1
    ? capOccurrences(datasets[0].configuredCaps.filter((cap) => cap.configuredOn !== 'compound'))
    : new Map<string, number>()
  for (const dataset of datasets.slice(1)) {
    const datasetOccurrences = capOccurrences(
      dataset.configuredCaps.filter((cap) => cap.configuredOn !== 'compound'),
    )
    for (const [key, count] of universalRowCapOccurrences) {
      universalRowCapOccurrences.set(key, Math.min(count, datasetOccurrences.get(key) ?? 0))
    }
  }

  const emittedRowCapOccurrences = new Map<string, number>()
  if (datasets.length > 0) {
    for (const cap of datasets[0].configuredCaps) {
      if (cap.configuredOn === 'compound') {
        continue
      }
      const key = semanticCapKey(cap)
      const emitted = emittedRowCapOccurrences.get(key) ?? 0
      if (emitted < (universalRowCapOccurrences.get(key) ?? 0)) {
        commonCaps.push(cap)
        emittedRowCapOccurrences.set(key, emitted + 1)
      }
    }
  }

  const residualCapsByDatasetId = new Map<string, ScenarioRunEstimateDatasetCap[]>()
  for (const dataset of datasets) {
    const consumedRowCapOccurrences = new Map<string, number>()
    const residualCaps = dataset.configuredCaps.filter((cap) => {
      if (cap.configuredOn === 'compound') {
        return false
      }
      const key = semanticCapKey(cap)
      const consumed = consumedRowCapOccurrences.get(key) ?? 0
      if (consumed < (universalRowCapOccurrences.get(key) ?? 0)) {
        consumedRowCapOccurrences.set(key, consumed + 1)
        return false
      }
      return true
    })
    residualCapsByDatasetId.set(dataset.id, residualCaps)
  }

  return { commonCaps, residualCapsByDatasetId }
}
