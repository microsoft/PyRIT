import type {
  ScenarioRunEstimate,
  ScenarioRunEstimateDataset,
  ScenarioRunEstimateDatasetCap,
  ScenarioRunEstimateResult,
  ScenarioRunSizeEstimateResponse,
} from '@/types'

function nextStableId(prefix: string, label: string, occurrences: Map<string, number>): string {
  const occurrence = (occurrences.get(label) ?? 0) + 1
  occurrences.set(label, occurrence)
  return `${prefix}:${label}:${occurrence}`
}

function mapDatasetCaps(
  datasetId: string,
  caps: ScenarioRunSizeEstimateResponse['datasets'][number]['configured_caps'],
): ScenarioRunEstimateDatasetCap[] {
  const occurrences = new Map<string, number>()
  return caps.map((cap) => ({
    id: nextStableId(`${datasetId}:cap`, cap.label, occurrences),
    label: cap.label,
    count: cap.count,
    configuredOn: cap.configured_on,
    datasetName: cap.dataset_name,
  }))
}

function mapDatasets(
  datasets: ScenarioRunSizeEstimateResponse['datasets'],
): ScenarioRunEstimateDataset[] {
  const occurrences = new Map<string, number>()
  return datasets.map((dataset) => {
    const id = nextStableId('dataset', dataset.name, occurrences)
    return {
      id,
      name: dataset.name,
      kind: dataset.kind,
      logicalSeedGroupCount: dataset.logical_seed_group_count,
      selectedSeedGroupCount: dataset.selected_seed_group_count,
      configuredCaps: mapDatasetCaps(id, dataset.configured_caps),
      selectionNote: dataset.selection_note,
    }
  })
}

export function mapScenarioRunEstimate(
  response: ScenarioRunSizeEstimateResponse,
  scope: ScenarioRunEstimate['scope'],
): ScenarioRunEstimateResult {
  if (
    response.estimated_attack_count === null
    && response.minimum_attack_count == null
    && response.maximum_attack_count == null
    && response.components.length === 0
  ) {
    return {
      status: 'unavailable',
      scope,
      label: scope === 'default'
        ? 'Default run size unavailable'
        : 'Configured run size unavailable',
      note: response.note ?? undefined,
    }
  }

  const componentOccurrences = new Map<string, number>()
  const estimate: ScenarioRunEstimate = {
    scope,
    total: response.estimated_attack_count,
    minimum: response.minimum_attack_count ?? null,
    maximum: response.maximum_attack_count ?? null,
    components: response.components.map((component) => {
      const id = nextStableId('component', component.label, componentOccurrences)
      return {
        id,
        label: component.label,
        count: component.count,
        isBaseline: component.is_baseline,
        note: component.note,
      }
    }),
    datasets: mapDatasets(response.datasets),
    effectiveParameters: response.effective_parameters ?? {},
    note: response.note,
  }

  return response.estimated_attack_count === null
    ? { status: 'conditional', estimate }
    : { status: 'available', estimate }
}
