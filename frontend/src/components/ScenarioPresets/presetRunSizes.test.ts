import { scenariosApi } from '@/services/api'
import type {
  ScenarioPreset,
  ScenarioPresetResponse,
  ScenarioRunSizeEstimateResponse,
} from '@/types'

import { estimatePresetRunSizes } from './presetRunSizes'

jest.mock('@/services/api', () => ({
  scenariosApi: {
    estimateRun: jest.fn(),
  },
}))

const mockEstimateRun = scenariosApi.estimateRun as jest.Mock

function makeItem(preset: Partial<ScenarioPreset> & { name: string }): ScenarioPresetResponse {
  return {
    preset: { scenario_name: 'foundry.red_team_agent', techniques: ['crescendo'], ...preset },
    origin: 'stored',
    version: 'v1',
    issues: [],
    run_size: null,
  }
}

function makeRunSize(attacks: number): ScenarioRunSizeEstimateResponse {
  return {
    status: 'exact',
    dataset_size: { kind: 'bounded', value: attacks },
    dataset_limit: { state: 'value', value: attacks },
    estimated_attack_count: attacks,
    components: [{ label: 'Crescendo', count: attacks, is_baseline: false, note: null }],
    datasets: [],
    note: null,
  }
}

function httpError(status: number, detail: string): unknown {
  return { isAxiosError: true, response: { status, data: { detail } } }
}

describe('estimatePresetRunSizes', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockEstimateRun.mockReset()
  })

  it('resolves to no run sizes or failures for no presets', async () => {
    const result = await estimatePresetRunSizes([], new AbortController().signal)

    expect(result.runSizes.size).toBe(0)
    expect(result.failures).toEqual([])
    expect(mockEstimateRun).not.toHaveBeenCalled()
  })

  it('estimates each preset with its own configuration and the caller signal', async () => {
    const controller = new AbortController()
    const estimate = makeRunSize(24)
    mockEstimateRun.mockResolvedValueOnce(estimate)

    const result = await estimatePresetRunSizes([makeItem({
      name: 'nightly_probe',
      datasets: { sources: [{ name: 'harmbench' }], max_per_dataset: 3, max_total: 12 },
      include_baseline: false,
      scenario_params: { max_turns: 4 },
    })], controller.signal)

    expect(mockEstimateRun).toHaveBeenCalledTimes(1)
    const [scenarioName, request, signal] = mockEstimateRun.mock.calls[0]
    expect(scenarioName).toBe('foundry.red_team_agent')
    expect(request).toEqual({
      techniques: ['crescendo'],
      datasets: { sources: [{ name: 'harmbench' }], max_per_dataset: 3, max_total: 12 },
      include_baseline: false,
      scenario_params: { max_turns: 4 },
    })
    expect(signal).toBe(controller.signal)
    expect(result.runSizes.get('nightly_probe')).toBe(estimate)
    expect(result.failures).toEqual([])
  })

  it('omits null datasets so the scenario defaults apply', async () => {
    mockEstimateRun.mockResolvedValueOnce(makeRunSize(12))

    await estimatePresetRunSizes(
      [makeItem({ name: 'nightly_probe', datasets: null })],
      new AbortController().signal,
    )

    expect(mockEstimateRun.mock.calls[0][1].datasets).toBeUndefined()
  })

  it('skips presets with advisory issues without requesting an estimate', async () => {
    mockEstimateRun.mockResolvedValueOnce(makeRunSize(12))
    const unresolvable: ScenarioPresetResponse = {
      ...makeItem({ name: 'retired_probe', scenario_name: 'foundry.retired' }),
      issues: [{ field: 'scenario_name', message: 'Unknown scenario "foundry.retired".' }],
    }

    const result = await estimatePresetRunSizes(
      [unresolvable, makeItem({ name: 'nightly_probe' })],
      new AbortController().signal,
    )

    expect(mockEstimateRun).toHaveBeenCalledTimes(1)
    expect(mockEstimateRun.mock.calls[0][0]).toBe('foundry.red_team_agent')
    expect(result.runSizes.has('retired_probe')).toBe(true)
    expect(result.runSizes.get('retired_probe')).toBeNull()
    expect(result.runSizes.get('nightly_probe')).toEqual(makeRunSize(12))
    expect(result.failures).toEqual([])
  })

  it('keeps successful estimates and reports each failure by preset name', async () => {
    mockEstimateRun
      .mockResolvedValueOnce(makeRunSize(7))
      .mockRejectedValueOnce(new Error('Invalid configuration.'))
      .mockRejectedValueOnce(httpError(422, 'Unknown technique.'))

    const result = await estimatePresetRunSizes(
      [makeItem({ name: 'a' }), makeItem({ name: 'b' }), makeItem({ name: 'c' })],
      new AbortController().signal,
    )

    expect(Array.from(result.runSizes.entries())).toEqual([
      ['a', makeRunSize(7)],
      ['b', null],
      ['c', null],
    ])
    expect(result.failures).toEqual(['b: Invalid configuration.', 'c: Unknown technique.'])
  })

  it('resolves rather than rejects when every estimate fails', async () => {
    mockEstimateRun.mockRejectedValue(httpError(503, 'Scenario service unavailable.'))

    await expect(estimatePresetRunSizes(
      [makeItem({ name: 'a' }), makeItem({ name: 'b' })],
      new AbortController().signal,
    )).resolves.toEqual({
      runSizes: new Map([['a', null], ['b', null]]),
      failures: ['a: Scenario service unavailable.', 'b: Scenario service unavailable.'],
    })
  })
})
