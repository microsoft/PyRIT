import { act, renderHook } from '@testing-library/react'

import { scenarioPresetsApi, scenarioSuitesApi } from '@/services/api'
import { makePresetResponse, makeSuite } from '@/test-utils/scenarioSuiteFixtures'
import type { ScenarioPresetResponse, ScenarioSuite } from '@/types'

import { type SuiteMutationOutcome, useScenarioSuiteMutations } from './useScenarioSuiteMutations'

jest.mock('@/services/api', () => ({
  scenarioPresetsApi: { create: jest.fn() },
  scenarioSuitesApi: { get: jest.fn(), create: jest.fn(), update: jest.fn(), remove: jest.fn() },
}))

const mockCreatePreset = scenarioPresetsApi.create as jest.Mock
const mockCreateSuite = scenarioSuitesApi.create as jest.Mock
const mockGetSuite = scenarioSuitesApi.get as jest.Mock
const mockUpdateSuite = scenarioSuitesApi.update as jest.Mock
const mockRemoveSuite = scenarioSuitesApi.remove as jest.Mock

const EXISTING = makePresetResponse({ name: 'release_gate_airt_jailbreak', scenario_name: 'airt.jailbreak' })
const PRESETS: ReadonlyMap<string, ScenarioPresetResponse> = new Map([[EXISTING.preset.name, EXISTING]])
const SUITE = makeSuite('release_gate', ['smoke'], { description: 'Before every release.' })

function apiError(status: number, detail: string): unknown {
  return { isAxiosError: true, response: { status, data: { detail } } }
}

function renderMutations() {
  const onChanged = jest.fn()
  const view = renderHook(() => useScenarioSuiteMutations(PRESETS, onChanged))
  return { ...view, onChanged }
}

async function run(write: () => Promise<SuiteMutationOutcome>): Promise<SuiteMutationOutcome> {
  let outcome: SuiteMutationOutcome = { status: 'failed', error: 'not run' }
  await act(async () => { outcome = await write() })
  return outcome
}

function savedSuite(mock: jest.Mock, call = 0, argument = 0): ScenarioSuite {
  return mock.mock.calls[call][argument] as ScenarioSuite
}

describe('useScenarioSuiteMutations', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockCreatePreset.mockImplementation(async (preset) => makePresetResponse(preset))
    mockCreateSuite.mockResolvedValue(SUITE)
    mockUpdateSuite.mockResolvedValue(SUITE)
    mockRemoveSuite.mockResolvedValue(undefined)
  })

  it('creates the first scenario with scenario defaults, then the preset that holds it', async () => {
    const { result, onChanged } = renderMutations()

    const outcome = await run(() => result.current.createSuite({
      name: 'nightly', description: '  Every night.  ', scenarioName: 'garak.encoding',
    }))

    expect(outcome).toEqual({ status: 'done' })
    expect(mockCreatePreset).toHaveBeenCalledWith({ name: 'nightly_garak_encoding', scenario_name: 'garak.encoding' })
    const suite = savedSuite(mockCreateSuite)
    expect(suite).toMatchObject({ name: 'nightly', schema_version: 1, description: 'Every night.' })
    expect(suite.entries).toEqual([{ entry_id: expect.any(String), preset_name: 'nightly_garak_encoding' }])
    expect(mockCreatePreset.mock.invocationCallOrder[0]).toBeLessThan(mockCreateSuite.mock.invocationCallOrder[0] ?? 0)
    expect(onChanged).toHaveBeenCalledTimes(1)
  })

  it('reports a saved scenario that the new preset could not hold, and retries without saving it twice', async () => {
    const { result } = renderMutations()
    mockCreateSuite.mockRejectedValueOnce(apiError(409, "Suite 'nightly' already exists."))
    const input = { name: 'nightly', description: '', scenarioName: 'garak.encoding' }

    const outcome = await run(() => result.current.createSuite(input))

    expect(outcome).toEqual({
      status: 'partial',
      error: "Suite 'nightly' already exists.",
      savedPresetName: 'nightly_garak_encoding',
    })
    const retried = await run(() => result.current.createSuite({ ...input, name: 'nightly_two' }, 'nightly_garak_encoding'))
    expect(retried).toEqual({ status: 'done' })
    expect(mockCreatePreset).toHaveBeenCalledTimes(1)
    expect(savedSuite(mockCreateSuite, 1).entries[0]?.preset_name).toBe('nightly_garak_encoding')
  })

  it('stops before the preset when the scenario cannot be saved', async () => {
    const { result } = renderMutations()
    mockCreatePreset.mockRejectedValueOnce(apiError(403, 'Admin permission required.'))

    const outcome = await run(() => result.current.createSuite({ name: 'nightly', description: '', scenarioName: 'x.y' }))

    expect(outcome).toEqual({ status: 'failed', error: 'Admin permission required.' })
    expect(mockCreateSuite).not.toHaveBeenCalled()
  })

  it('adds a scenario under a unique internal name with the version it read', async () => {
    const { result } = renderMutations()

    const outcome = await run(() => result.current.addScenario(SUITE, 'airt.jailbreak'))

    expect(outcome).toEqual({ status: 'done' })
    expect(mockCreatePreset).toHaveBeenCalledWith({ name: 'release_gate_airt_jailbreak_2', scenario_name: 'airt.jailbreak' })
    expect(mockUpdateSuite).toHaveBeenCalledWith('release_gate', expect.anything(), 'release_gate-v1')
    expect(savedSuite(mockUpdateSuite, 0, 1).entries.map((entry) => entry.preset_name))
      .toEqual(['smoke', 'release_gate_airt_jailbreak_2'])
  })

  it('says the scenario was saved but not added when the preset changed in between', async () => {
    const { result, onChanged } = renderMutations()
    mockUpdateSuite.mockRejectedValueOnce(apiError(409, 'Version mismatch.'))

    const outcome = await run(() => result.current.addScenario(SUITE, 'airt.jailbreak'))

    expect(outcome).toEqual({
      status: 'partial',
      error: "'release_gate' changed since it was loaded. Review it and try again.",
      savedPresetName: 'release_gate_airt_jailbreak_2',
    })
    expect(onChanged).toHaveBeenCalledTimes(1)
  })

  it('retries adding to the preset as it is now, with its reloaded version and scenarios', async () => {
    const { result } = renderMutations()
    const reloaded = { ...makeSuite('release_gate', ['smoke', 'added_elsewhere']), version: 'release_gate-v2' }
    mockGetSuite.mockResolvedValueOnce(reloaded)

    // The stale snapshot the dialog opened with is what a retry is handed.
    const outcome = await run(() => result.current.addScenario(SUITE, 'airt.jailbreak', 'release_gate_airt_jailbreak_2'))

    expect(outcome).toEqual({ status: 'done' })
    expect(mockCreatePreset).not.toHaveBeenCalled()
    expect(mockGetSuite).toHaveBeenCalledWith('release_gate')
    expect(mockUpdateSuite).toHaveBeenCalledWith('release_gate', expect.anything(), 'release_gate-v2')
    expect(savedSuite(mockUpdateSuite, 0, 1).entries.map((entry) => entry.preset_name))
      .toEqual(['smoke', 'added_elsewhere', 'release_gate_airt_jailbreak_2'])
  })

  it('refuses to change a preset defined in code', async () => {
    const { result } = renderMutations()
    const registered = makeSuite('builtin', ['smoke'], { origin: 'registered' })

    const outcome = await run(() => result.current.addScenario(registered, 'airt.jailbreak'))

    expect(outcome).toEqual({ status: 'failed', error: "'builtin' is defined in code and cannot be changed here." })
    expect(mockCreatePreset).not.toHaveBeenCalled()
    expect(await run(() => result.current.removeEntry(registered, 'builtin-entry-1'))).toMatchObject({ status: 'failed' })
    expect(mockUpdateSuite).not.toHaveBeenCalled()
  })

  it('removes one entry and keeps the rest in order', async () => {
    const { result } = renderMutations()
    const suite = makeSuite('release_gate', ['a', 'b', 'c'])

    await run(() => result.current.removeEntry(suite, 'release_gate-entry-2'))

    expect(savedSuite(mockUpdateSuite, 0, 1).entries.map((entry) => entry.preset_name)).toEqual(['a', 'c'])
    expect(mockCreatePreset).not.toHaveBeenCalled()
  })

  it('updates the description and drops it when cleared', async () => {
    const { result } = renderMutations()

    await run(() => result.current.updateDescription(SUITE, ' Weekly. '))
    await run(() => result.current.updateDescription(SUITE, '   '))

    expect(savedSuite(mockUpdateSuite, 0, 1).description).toBe('Weekly.')
    expect(savedSuite(mockUpdateSuite, 1, 1)).not.toHaveProperty('description')
    expect(savedSuite(mockUpdateSuite, 1, 1).entries).toEqual(SUITE.suite.entries)
  })

  it('deletes with the version it read and explains a conflict', async () => {
    const { result } = renderMutations()
    mockRemoveSuite.mockRejectedValueOnce(apiError(409, 'Version mismatch.'))

    const outcome = await run(() => result.current.deleteSuite(SUITE))

    expect(mockRemoveSuite).toHaveBeenCalledWith('release_gate', 'release_gate-v1')
    expect(outcome).toEqual({
      status: 'failed',
      error: "'release_gate' changed since it was loaded. Review it and try again.",
    })
    expect(await run(() => result.current.deleteSuite(SUITE))).toEqual({ status: 'done' })
  })

  it('is busy while a write is in flight', async () => {
    const { result } = renderMutations()
    let release: () => void = () => {}
    mockRemoveSuite.mockReturnValueOnce(new Promise<void>((resolve) => { release = resolve }))

    let pending: Promise<SuiteMutationOutcome> = Promise.resolve({ status: 'done' })
    act(() => { pending = result.current.deleteSuite(SUITE) })
    expect(result.current.busy).toBe(true)
    await act(async () => {
      release()
      await pending
    })
    expect(result.current.busy).toBe(false)
  })
})
