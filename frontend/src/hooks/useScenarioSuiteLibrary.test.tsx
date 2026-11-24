import { act, renderHook, waitFor } from '@testing-library/react'

import { scenarioPresetsApi, scenariosApi, scenarioSuitesApi } from '@/services/api'
import { advanceAutoRefresh, installAutoRefreshTimers } from '@/test-utils/autoRefresh'
import { deferred } from '@/test-utils/deferred'
import { makeScenario } from '@/test-utils/scenarioFixtures'
import { makePresetResponse, makeRunSize, makeSuite } from '@/test-utils/scenarioSuiteFixtures'
import type { ScenarioRunSizeEstimateResponse } from '@/types'

import { useScenarioSuiteLibrary } from './useScenarioSuiteLibrary'

installAutoRefreshTimers()

jest.mock('@/services/api', () => ({
  scenarioSuitesApi: { list: jest.fn() },
  scenarioPresetsApi: { list: jest.fn() },
  scenariosApi: { listCatalog: jest.fn(), estimateRun: jest.fn() },
}))

const mockListSuites = scenarioSuitesApi.list as jest.Mock
const mockListPresets = scenarioPresetsApi.list as jest.Mock
const mockListCatalog = scenariosApi.listCatalog as jest.Mock
const mockEstimateRun = scenariosApi.estimateRun as jest.Mock

const JAILBREAK = makeScenario({ scenario_name: 'airt.jailbreak' })
const SMOKE = makePresetResponse({ name: 'smoke', scenario_name: 'airt.jailbreak', techniques: ['crescendo'] })
const UNUSED = makePresetResponse({ name: 'unused', scenario_name: 'airt.jailbreak' })

describe('useScenarioSuiteLibrary', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockListSuites.mockResolvedValue({ source: 'suites', items: [makeSuite('release_gate', ['smoke'])] })
    mockListPresets.mockResolvedValue({ source: 'presets', items: [SMOKE, UNUSED] })
    mockListCatalog.mockResolvedValue({ items: [JAILBREAK], pagination: { limit: 200, has_more: false } })
    mockEstimateRun.mockResolvedValue(makeRunSize(90))
  })

  it('loads presets, the scenarios they hold, and registered scenarios by name', async () => {
    const { result } = renderHook(() => useScenarioSuiteLibrary())

    expect(result.current.loading).toBe(true)
    await waitFor(() => expect(result.current.loading).toBe(false))

    expect(result.current.suites.map((item) => item.suite.name)).toEqual(['release_gate'])
    expect(result.current.presets.get('smoke')?.preset).toEqual(SMOKE.preset)
    expect(result.current.scenarios.get('airt.jailbreak')).toEqual(JAILBREAK)
    expect(mockListPresets).toHaveBeenCalledWith(expect.any(AbortSignal), false)
    expect(mockListCatalog).toHaveBeenCalledWith(200, undefined, false)
  })

  it('estimates only the scenarios a preset uses, each with its own settings', async () => {
    const { result } = renderHook(() => useScenarioSuiteLibrary())

    await waitFor(() => expect(result.current.estimatesLoading).toBe(false))

    expect(mockEstimateRun).toHaveBeenCalledTimes(1)
    expect(mockEstimateRun).toHaveBeenCalledWith(
      'airt.jailbreak',
      expect.objectContaining({ techniques: ['crescendo'] }),
      expect.any(AbortSignal),
    )
    expect(result.current.presets.get('smoke')?.run_size).toEqual(makeRunSize(90))
    expect(result.current.presets.get('unused')?.run_size).toBeUndefined()
  })

  it('reports a failed read loudly and recovers on reload', async () => {
    mockListSuites.mockRejectedValueOnce(new Error('Suite storage is unavailable.'))
    const { result } = renderHook(() => useScenarioSuiteLibrary())

    await waitFor(() => expect(result.current.error).toBe('Suite storage is unavailable.'))
    expect(result.current.loading).toBe(false)
    expect(result.current.estimatesLoading).toBe(false)

    act(() => result.current.reload())

    await waitFor(() => expect(result.current.error).toBeNull())
    expect(result.current.suites).toHaveLength(1)
  })

  it('names each scenario whose run size could not be estimated', async () => {
    mockEstimateRun.mockRejectedValue(new Error('Estimator offline.'))
    const { result } = renderHook(() => useScenarioSuiteLibrary())

    await waitFor(() => expect(result.current.estimateError).toBe('smoke: Estimator offline.'))
    expect(result.current.presets.get('smoke')?.run_size).toBeNull()
  })

  it('keeps shown estimates while an automatic refresh re-estimates', async () => {
    const next = deferred<ScenarioRunSizeEstimateResponse>()
    const { result } = renderHook(() => useScenarioSuiteLibrary())
    await waitFor(() => expect(result.current.presets.get('smoke')?.run_size).toEqual(makeRunSize(90)))

    mockEstimateRun.mockReturnValueOnce(next.promise)
    await advanceAutoRefresh()

    expect(mockListSuites).toHaveBeenCalledTimes(2)
    expect(result.current.presets.get('smoke')?.run_size).toEqual(makeRunSize(90))
    await act(async () => { next.resolve(makeRunSize(120)) })
    expect(result.current.presets.get('smoke')?.run_size).toEqual(makeRunSize(120))
  })

  it('aborts its reads on unmount', async () => {
    const { unmount } = renderHook(() => useScenarioSuiteLibrary())
    await waitFor(() => expect(mockListSuites).toHaveBeenCalled())
    const signal = mockListSuites.mock.calls[0][0] as AbortSignal

    unmount()

    expect(signal.aborted).toBe(true)
  })
})
