import { useCallback, useEffect, useRef, useState } from 'react'

import { useAutoRefresh } from '@/hooks/useAutoRefresh'
import { useRuntime } from '@/hooks/useRuntime'
import { scenariosApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { RegisteredScenario, ScenarioModule } from '@/types'
import { fetchAllPages } from '@/utils/fetchAllPages'

/** Items requested per catalog page while paging through the full list. */
const CATALOG_PAGE_SIZE = 200

interface ScenarioCatalogState {
  readonly scenarios: RegisteredScenario[]
  readonly modules: ScenarioModule[]
  readonly loading: boolean
  readonly estimatesLoading: boolean
  /** Why the scenarios or modules could not be loaded. */
  readonly error: string | null
  /** Why default run sizes could not be loaded; the scenarios still show. */
  readonly estimateError: string | null
  /** Loads everything again, showing the loading state until it finishes. */
  readonly retry: () => void
}

function listScenarios(includeEstimates: boolean): Promise<RegisteredScenario[]> {
  return fetchAllPages(
    (cursor) => scenariosApi.listCatalog(CATALOG_PAGE_SIZE, cursor, includeEstimates),
    undefined,
    (scenario) => scenario.scenario_name,
  )
}

/** Keeps a scenario's known default estimate while a quiet reload brings unchanged metadata. */
function mergeKnownEstimates(current: RegisteredScenario[], items: RegisteredScenario[]): RegisteredScenario[] {
  return items.map((item) => {
    const previous = current.find((candidate) => candidate.scenario_name === item.scenario_name)
    return previous
      && JSON.stringify({ ...previous, default_run_size: null }) === JSON.stringify({ ...item, default_run_size: null })
      ? { ...item, default_run_size: previous.default_run_size }
      : item
  })
}

/**
 * The Launch tab's scenario catalog and modules. Definitions load first so the list shows quickly;
 * default run-size estimates follow. The catalog revalidates in the background.
 */
export function useScenarioCatalog(): ScenarioCatalogState {
  const { generation } = useRuntime()
  const [scenarios, setScenarios] = useState<RegisteredScenario[]>([])
  const [modules, setModules] = useState<ScenarioModule[]>([])
  const [loading, setLoading] = useState(true)
  const [estimatesLoading, setEstimatesLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [estimateError, setEstimateError] = useState<string | null>(null)
  const [refetchCount, setRefetchCount] = useState(0)
  const pending = useRef(false)

  useEffect(() => {
    let cancelled = false
    pending.current = true

    const loadScenarioEstimates = async (): Promise<void> => {
      try {
        const estimatedItems = await listScenarios(true)
        if (cancelled) return
        setScenarios(estimatedItems)
        setEstimateError(null)
      } catch (err: unknown) {
        if (cancelled) return
        setEstimateError(toApiError(err).detail)
      } finally {
        if (!cancelled) {
          setEstimatesLoading(false)
        }
      }
    }

    const loadCatalog = async (): Promise<void> => {
      let items: RegisteredScenario[]
      let moduleItems: ScenarioModule[]
      try {
        const [scenarioItems, moduleResponse] = await Promise.all([
          listScenarios(false),
          scenariosApi.listModules(),
        ])
        items = scenarioItems
        moduleItems = moduleResponse.items
      } catch (err: unknown) {
        if (cancelled) return
        setError(toApiError(err).detail)
        setLoading(false)
        setEstimatesLoading(false)
        return
      }
      if (cancelled) return

      setScenarios((current) => mergeKnownEstimates(current, items))
      setModules(moduleItems)
      setError(null)
      setLoading(false)

      if (items.length === 0) {
        setEstimatesLoading(false)
        return
      }
      await loadScenarioEstimates()
    }

    void loadCatalog().finally(() => { if (!cancelled) pending.current = false })

    return () => {
      cancelled = true
      pending.current = false
    }
  }, [refetchCount, generation])

  const reload = useCallback(() => {
    setRefetchCount((count) => count + 1)
  }, [])
  const retry = useCallback(() => {
    setError(null)
    setLoading(true)
    setEstimatesLoading(true)
    reload()
  }, [reload])
  useAutoRefresh(() => { if (!pending.current) reload() }, !loading)

  return { scenarios, modules, loading, estimatesLoading, error, estimateError, retry }
}
