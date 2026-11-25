import { act, renderHook, waitFor } from '@testing-library/react'

import { scenariosApi } from '@/services/api'
import { advanceAutoRefresh, installAutoRefreshTimers } from '@/test-utils/autoRefresh'
import { deferred } from '@/test-utils/deferred'
import { makeScenario } from '@/test-utils/scenarioFixtures'
import { makeRunSize } from '@/test-utils/scenarioSuiteFixtures'
import type { ListRegisteredScenariosResponse, RegisteredScenario, ScenarioModule } from '@/types'

import { useScenarioCatalog } from './useScenarioCatalog'

installAutoRefreshTimers()

jest.mock('@/services/api', () => ({
  scenariosApi: {
    listCatalog: jest.fn(),
    listModules: jest.fn(),
  },
}))

const mockListCatalog = scenariosApi.listCatalog as jest.Mock
const mockListModules = scenariosApi.listModules as jest.Mock

const MODULES: ScenarioModule[] = [{ name: 'garak', display_name: 'Garak', description: 'Ports of garak probes.' }]

function catalogPage(
  items: RegisteredScenario[],
  nextCursor: string | null = null,
): ListRegisteredScenariosResponse {
  return { items, pagination: { limit: 200, has_more: nextCursor !== null, next_cursor: nextCursor } }
}

function names(scenarios: RegisteredScenario[]): string[] {
  return scenarios.map((scenario) => scenario.scenario_name)
}

describe('useScenarioCatalog', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockListModules.mockResolvedValue({ items: MODULES })
  })

  it('loads definitions and modules first, then default estimates', async () => {
    const scenario = makeScenario({ scenario_name: 'garak.encoding', module: 'garak' })
    const estimates = deferred<ListRegisteredScenariosResponse>()
    mockListCatalog
      .mockResolvedValueOnce(catalogPage([scenario]))
      .mockReturnValueOnce(estimates.promise)

    const { result } = renderHook(() => useScenarioCatalog())
    expect(result.current.loading).toBe(true)

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.modules).toEqual(MODULES)
    expect(names(result.current.scenarios)).toEqual(['garak.encoding'])
    expect(result.current.estimatesLoading).toBe(true)
    expect(mockListCatalog).toHaveBeenNthCalledWith(1, 200, undefined, false)

    await act(async () => {
      estimates.resolve(catalogPage([{ ...scenario, default_run_size: makeRunSize(6) }]))
    })
    expect(mockListCatalog).toHaveBeenNthCalledWith(2, 200, undefined, true)
    expect(result.current.estimatesLoading).toBe(false)
    expect(result.current.scenarios[0].default_run_size.estimated_attack_count).toBe(6)
  })

  it('follows the cursor to load every page', async () => {
    mockListCatalog.mockImplementation((_limit: number, cursor?: string) => Promise.resolve(cursor
      ? catalogPage([makeScenario({ scenario_name: 'scenario.page2' })])
      : catalogPage([makeScenario({ scenario_name: 'scenario.page1' })], 'cursor-1')))

    const { result } = renderHook(() => useScenarioCatalog())

    await waitFor(() => expect(result.current.estimatesLoading).toBe(false))
    expect(names(result.current.scenarios)).toEqual(['scenario.page1', 'scenario.page2'])
    expect(mockListCatalog).toHaveBeenCalledWith(200, 'cursor-1', false)
    expect(mockListCatalog).toHaveBeenCalledWith(200, 'cursor-1', true)
  })

  it('stops paging if the backend repeats a cursor', async () => {
    mockListCatalog.mockResolvedValue(catalogPage([makeScenario({ scenario_name: 'scenario.loop' })], 'same-cursor'))

    const { result } = renderHook(() => useScenarioCatalog())

    await waitFor(() => expect(result.current.estimatesLoading).toBe(false))
    expect(names(result.current.scenarios)).toEqual(['scenario.loop'])
    expect(mockListCatalog).toHaveBeenCalledTimes(4)
  })

  it('keeps known estimates while a background reload calculates new ones', async () => {
    const scenario = makeScenario({ scenario_name: 'review.scan' })
    const nextEstimates = deferred<ListRegisteredScenariosResponse>()
    mockListCatalog
      .mockResolvedValueOnce(catalogPage([scenario]))
      .mockResolvedValueOnce(catalogPage([{ ...scenario, default_run_size: makeRunSize(6) }]))
      .mockResolvedValueOnce(catalogPage([scenario]))
      .mockReturnValueOnce(nextEstimates.promise)
    const { result } = renderHook(() => useScenarioCatalog())
    await waitFor(() => expect(result.current.scenarios[0]?.default_run_size.estimated_attack_count).toBe(6))

    await advanceAutoRefresh()

    expect(mockListCatalog).toHaveBeenCalledTimes(4)
    expect(result.current.scenarios[0].default_run_size.estimated_attack_count).toBe(6)
    await act(async () => {
      nextEstimates.resolve(catalogPage([{ ...scenario, default_run_size: makeRunSize(12) }]))
    })
    expect(result.current.scenarios[0].default_run_size.estimated_attack_count).toBe(12)
  })

  it('keeps the scenarios when default estimates fail', async () => {
    mockListCatalog
      .mockResolvedValueOnce(catalogPage([makeScenario({ scenario_name: 'scenario.unsized' })]))
      .mockRejectedValueOnce(new Error('estimate failed'))

    const { result } = renderHook(() => useScenarioCatalog())

    await waitFor(() => expect(result.current.estimateError).toBe('estimate failed'))
    expect(names(result.current.scenarios)).toEqual(['scenario.unsized'])
    expect(result.current.estimatesLoading).toBe(false)
    expect(result.current.error).toBeNull()
  })

  it('reports a module list failure as a catalog error', async () => {
    mockListCatalog.mockResolvedValue(catalogPage([makeScenario({ scenario_name: 'garak.encoding' })]))
    mockListModules.mockRejectedValueOnce(new Error('modules failed'))

    const { result } = renderHook(() => useScenarioCatalog())

    await waitFor(() => expect(result.current.error).toBe('modules failed'))
    expect(result.current.loading).toBe(false)
    expect(result.current.scenarios).toEqual([])
  })

  it('shows the loading state again when retrying after a failure', async () => {
    const retried = deferred<ListRegisteredScenariosResponse>()
    mockListCatalog
      .mockRejectedValueOnce(new Error('boom'))
      .mockReturnValueOnce(retried.promise)
      .mockResolvedValue(catalogPage([makeScenario({ scenario_name: 'scenario.recovered' })]))
    const { result } = renderHook(() => useScenarioCatalog())
    await waitFor(() => expect(result.current.error).toBe('boom'))

    act(() => result.current.retry())

    expect(result.current.loading).toBe(true)
    expect(result.current.error).toBeNull()
    await act(async () => {
      retried.resolve(catalogPage([makeScenario({ scenario_name: 'scenario.recovered' })]))
    })
    expect(names(result.current.scenarios)).toEqual(['scenario.recovered'])
    expect(result.current.loading).toBe(false)
  })

  it('recovers on the next automatic check', async () => {
    mockListCatalog
      .mockRejectedValueOnce(new Error('boom'))
      .mockResolvedValue(catalogPage([makeScenario({ scenario_name: 'scenario.recovered' })]))
    const { result } = renderHook(() => useScenarioCatalog())
    await waitFor(() => expect(result.current.error).toBe('boom'))

    await advanceAutoRefresh()

    await waitFor(() => expect(names(result.current.scenarios)).toEqual(['scenario.recovered']))
    expect(result.current.error).toBeNull()
  })

  it.each([
    ['definitions', 0],
    ['estimates', 1],
  ])('ignores %s that settle after unmount', async (_stage, settledCalls) => {
    const pending = deferred<ListRegisteredScenariosResponse>()
    for (let call = 0; call < settledCalls; call += 1) {
      mockListCatalog.mockResolvedValueOnce(catalogPage([makeScenario({ scenario_name: 'airt.jailbreak' })]))
    }
    mockListCatalog.mockReturnValueOnce(pending.promise)
    const { result, unmount } = renderHook(() => useScenarioCatalog())
    await waitFor(() => expect(mockListCatalog).toHaveBeenCalledTimes(settledCalls + 1))
    const before = result.current

    unmount()
    await act(async () => {
      pending.reject(new Error('late failure'))
    })

    expect(result.current).toBe(before)
  })
})
