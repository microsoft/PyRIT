import { scenariosApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { ScenarioPresetResponse, ScenarioRunSizeEstimateResponse } from '@/types'

export interface PresetRunSizes {
  /** Keyed by preset name. Null when the preset has advisory issues or its estimate failed. */
  runSizes: Map<string, ScenarioRunSizeEstimateResponse | null>
  /** One `name: detail` message for each preset whose estimate failed. */
  failures: string[]
}

/** Keeps displayed estimates while unchanged preset definitions are revalidated. */
export function retainPresetRunSizes(
  items: ScenarioPresetResponse[],
  previous: ScenarioPresetResponse[],
): ScenarioPresetResponse[] {
  const previousByName = new Map(previous.map((item) => [item.preset.name, item]))
  return items.map((item) => {
    const saved = previousByName.get(item.preset.name)
    return saved && JSON.stringify({ ...saved, run_size: null }) === JSON.stringify({ ...item, run_size: null })
      ? { ...item, run_size: item.run_size ?? saved.run_size }
      : item
  })
}

/**
 * Estimates each preset with its own techniques, datasets, and parameters.
 * Presets with advisory issues are skipped because this deployment cannot resolve them.
 */
export async function estimatePresetRunSizes(
  items: ScenarioPresetResponse[],
  signal: AbortSignal,
): Promise<PresetRunSizes> {
  const estimates = await Promise.allSettled(items.map((item) => (
    item.issues.length > 0 ? Promise.resolve(null) : scenariosApi.estimateRun(
      item.preset.scenario_name,
      {
        techniques: item.preset.techniques,
        datasets: item.preset.datasets ?? undefined,
        include_baseline: item.preset.include_baseline,
        scenario_params: item.preset.scenario_params,
      },
      signal,
    )
  )))
  const outcomes = items.map((item, index) => {
    const estimate = estimates[index]
    return estimate.status === 'fulfilled'
      ? { name: item.preset.name, runSize: estimate.value, failure: null }
      : {
        name: item.preset.name,
        runSize: null,
        failure: `${item.preset.name}: ${toApiError(estimate.reason).detail}`,
      }
  })
  return {
    runSizes: new Map<string, ScenarioRunSizeEstimateResponse | null>(
      outcomes.map((outcome) => [outcome.name, outcome.runSize]),
    ),
    failures: outcomes.flatMap((outcome) => (outcome.failure ? [outcome.failure] : [])),
  }
}
