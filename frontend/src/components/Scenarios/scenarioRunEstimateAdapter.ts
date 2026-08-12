import type {
  ScenarioDefaultRunSizeEstimate,
  ScenarioRunEstimate,
  ScenarioRunEstimateDataset,
  ScenarioRunEstimateDatasetCap,
  ScenarioRunEstimateFactor,
  ScenarioRunEstimateResult,
} from '@/types'

function nextStableId(prefix: string, label: string, occurrences: Map<string, number>): string {
  const occurrence = (occurrences.get(label) ?? 0) + 1
  occurrences.set(label, occurrence)
  return `${prefix}:${label}:${occurrence}`
}

function mapFactors(
  componentId: string,
  factors: ScenarioDefaultRunSizeEstimate['components'][number]['factors'],
): ScenarioRunEstimateFactor[] {
  const occurrences = new Map<string, number>()
  return factors.map((factor) => ({
    id: nextStableId(`${componentId}:factor`, factor.label, occurrences),
    label: factor.label,
    count: factor.count,
  }))
}

function mapDatasetCaps(
  datasetId: string,
  caps: ScenarioDefaultRunSizeEstimate['datasets'][number]['configured_caps'],
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
  datasets: ScenarioDefaultRunSizeEstimate['datasets'],
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
  response: ScenarioDefaultRunSizeEstimate,
  scope: ScenarioRunEstimate['scope'],
): ScenarioRunEstimateResult {
  if (response.status === 'unavailable') {
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
    version: response.version,
    scope,
    total: response.total_attack_count,
    minimum: response.minimum_attack_count ?? null,
    maximum: response.maximum_attack_count ?? null,
    condition: response.condition ?? null,
    components: response.components.map((component) => {
      const id = nextStableId('component', component.label, componentOccurrences)
      return {
        id,
        label: component.label,
        count: component.count,
        factors: mapFactors(id, component.factors),
        isBaseline: component.is_baseline,
        condition: component.condition ?? null,
        note: component.note,
      }
    }),
    datasets: mapDatasets(response.datasets),
    adaptiveDetails: response.adaptive_details
      ? {
          objectiveCount: response.adaptive_details.objective_count,
          selectedCandidateTechniqueCount: response.adaptive_details.selected_candidate_technique_count
            ?? response.adaptive_details.candidate_technique_count,
          candidateTechniqueCount: response.adaptive_details.candidate_technique_count,
          maxAttemptsPerObjective: response.adaptive_details.max_attempts_per_objective,
          techniquesPerObjectiveUpperBound: response.adaptive_details.techniques_per_objective_upper_bound,
          techniqueAttemptCountUpperBound: response.adaptive_details.technique_attempt_count_upper_bound,
          stopOnFirstSuccess: response.adaptive_details.stop_on_first_success,
          compatibilityMayReduceAttempts: response.adaptive_details.compatibility_may_reduce_attempts,
        }
      : null,
    note: response.note,
    retriesIncluded: response.retries_included,
  }

  return response.status === 'exact'
    ? { status: 'available', estimate }
    : { status: 'conditional', estimate }
}
