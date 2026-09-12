import type {
  ScenarioDefaultRunSizeEstimate,
  ScenarioRunEstimate,
  ScenarioRunEstimateAdaptiveDetails,
  ScenarioRunEstimateDataset,
  ScenarioRunEstimateDatasetCap,
  ScenarioRunEstimateFactor,
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

function mapFactors(
  componentId: string,
  factors: NonNullable<ScenarioDefaultRunSizeEstimate['components'][number]['factors']>,
): ScenarioRunEstimateFactor[] {
  const occurrences = new Map<string, number>()
  return factors.map((factor) => ({
    id: nextStableId(`${componentId}:factor`, factor.label, occurrences),
    label: factor.label,
    count: factor.count,
  }))
}

function mapAdaptiveDetails(
  details: NonNullable<ScenarioDefaultRunSizeEstimate['adaptive_details']>,
): ScenarioRunEstimateAdaptiveDetails {
  return {
    objectiveCount: details.objective_count,
    selectedCandidateTechniqueCount:
      details.selected_candidate_technique_count ?? details.candidate_technique_count,
    candidateTechniqueCount: details.candidate_technique_count,
    maxAttemptsPerObjective: details.max_attempts_per_objective,
    techniquesPerObjectiveUpperBound: details.techniques_per_objective_upper_bound,
    techniqueAttemptCountUpperBound: details.technique_attempt_count_upper_bound,
    stopOnFirstSuccess: details.stop_on_first_success,
    compatibilityMayReduceAttempts: details.compatibility_may_reduce_attempts,
  }
}

export function mapScenarioRunEstimate(
  response: ScenarioDefaultRunSizeEstimate | ScenarioRunSizeEstimateResponse,
  scope: ScenarioRunEstimate['scope'],
): ScenarioRunEstimateResult {
  const isRichEstimate = 'status' in response
  const total = isRichEstimate ? response.total_attack_count : response.estimated_attack_count
  const unavailable = isRichEstimate
    ? response.status === 'unavailable'
    : total === null
      && response.minimum_attack_count == null
      && response.maximum_attack_count == null
      && response.components.length === 0
  if (unavailable) {
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
    version: isRichEstimate ? response.version : 1,
    scope,
    total,
    minimum: response.minimum_attack_count ?? null,
    maximum: response.maximum_attack_count ?? null,
    condition: isRichEstimate ? response.condition ?? null : null,
    components: response.components.map((component) => {
      const id = nextStableId('component', component.label, componentOccurrences)
      return {
        id,
        label: component.label,
        count: component.count,
        factors: mapFactors(id, component.factors ?? []),
        isBaseline: component.is_baseline,
        condition: component.condition ?? null,
        note: component.note,
      }
    }),
    datasets: mapDatasets(response.datasets),
    adaptiveDetails: isRichEstimate && response.adaptive_details
      ? mapAdaptiveDetails(response.adaptive_details)
      : null,
    effectiveParameters: response.effective_parameters ?? {},
    note: response.note,
    retriesIncluded: isRichEstimate ? response.retries_included : false,
  }

  return (isRichEstimate ? response.status === 'conditional' : total === null)
    ? { status: 'conditional', estimate }
    : { status: 'available', estimate }
}
