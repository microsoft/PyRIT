import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { estimatePresetRunSizes, retainPresetRunSizes } from '@/components/ScenarioPresets/presetRunSizes'
import { useAutoRefresh } from '@/hooks/useAutoRefresh'
import { useRuntime } from '@/hooks/useRuntime'
import { scenarioPresetsApi, scenariosApi, scenarioSuitesApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { RegisteredScenario, ScenarioPresetResponse, ScenarioSuiteResponse } from '@/types'
import { fetchAllPages } from '@/utils/fetchAllPages'

/** Items requested per catalog page while paging through the full list. */
const CATALOG_PAGE_SIZE = 200

export interface ScenarioSuiteLibrary {
  readonly suites: ScenarioSuiteResponse[]
  /** Every stored and registered scenario preset by name, with its run size once estimated. */
  readonly presets: ReadonlyMap<string, ScenarioPresetResponse>
  /** Registered scenarios by name, for descriptions, picker options, and technique sets. */
  readonly scenarios: ReadonlyMap<string, RegisteredScenario>
  readonly loading: boolean
  readonly estimatesLoading: boolean
  /** Set when the suites, presets, or scenarios could not be read. */
  readonly error: string | null
  /** One `name: detail` message per scenario whose run size could not be estimated. */
  readonly estimateError: string | null
  readonly reload: () => void
}

function referencedPresets(
  suites: ScenarioSuiteResponse[],
  presets: ScenarioPresetResponse[],
): ScenarioPresetResponse[] {
  const names = new Set(suites.flatMap((item) => item.suite.entries.map((entry) => entry.preset_name)))
  return presets.filter((item) => names.has(item.preset.name))
}

/**
 * Loads the presets (backend scenario suites) with the scenarios inside them and their
 * run sizes, for both the Scanner's Presets tab and the Registry.
 */
export function useScenarioSuiteLibrary(): ScenarioSuiteLibrary {
  const { generation } = useRuntime()
  const [suites, setSuites] = useState<ScenarioSuiteResponse[]>([])
  const [presets, setPresets] = useState<ScenarioPresetResponse[]>([])
  const [scenarios, setScenarios] = useState<RegisteredScenario[]>([])
  const [loading, setLoading] = useState(true)
  const [estimatesLoading, setEstimatesLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [estimateError, setEstimateError] = useState<string | null>(null)
  const [revision, setRevision] = useState(0)
  const pending = useRef(false)

  useEffect(() => {
    const controller = new AbortController()
    let cancelled = false
    pending.current = true

    const load = async (): Promise<void> => {
      let listed: [ScenarioSuiteResponse[], ScenarioPresetResponse[], RegisteredScenario[]]
      try {
        const [suiteList, presetList, catalog] = await Promise.all([
          scenarioSuitesApi.list(controller.signal),
          scenarioPresetsApi.list(controller.signal, false),
          fetchAllPages(
            (cursor) => scenariosApi.listCatalog(CATALOG_PAGE_SIZE, cursor, false),
            undefined,
            (scenario) => scenario.scenario_name,
          ),
        ])
        listed = [suiteList.items, presetList.items, catalog]
      } catch (err: unknown) {
        if (cancelled) return
        setError(toApiError(err).detail)
        setLoading(false)
        setEstimatesLoading(false)
        return
      }
      if (cancelled) return

      const [nextSuites, nextPresets, nextScenarios] = listed
      setSuites(nextSuites)
      setPresets((current) => retainPresetRunSizes(nextPresets, current))
      setScenarios(nextScenarios)
      setError(null)
      setLoading(false)

      const toEstimate = referencedPresets(nextSuites, nextPresets)
      if (toEstimate.length === 0) {
        setEstimatesLoading(false)
        return
      }
      const { runSizes, failures } = await estimatePresetRunSizes(toEstimate, controller.signal)
      if (cancelled) return
      setPresets((current) => current.map((item) => (
        runSizes.has(item.preset.name) ? { ...item, run_size: runSizes.get(item.preset.name) ?? null } : item
      )))
      setEstimateError(failures.length > 0 ? failures.join('; ') : null)
      setEstimatesLoading(false)
    }

    void load().finally(() => { if (!cancelled) pending.current = false })
    return () => {
      cancelled = true
      controller.abort()
      pending.current = false
    }
  }, [generation, revision])

  const reload = useCallback((): void => {
    setRevision((current) => current + 1)
  }, [])
  useAutoRefresh(() => { if (!pending.current) reload() }, !loading)

  const presetsByName = useMemo(
    () => new Map(presets.map((item) => [item.preset.name, item])),
    [presets],
  )
  const scenariosByName = useMemo(
    () => new Map(scenarios.map((scenario) => [scenario.scenario_name, scenario])),
    [scenarios],
  )

  return {
    suites,
    presets: presetsByName,
    scenarios: scenariosByName,
    loading,
    estimatesLoading,
    error,
    estimateError,
    reload,
  }
}
