import { act, renderHook, waitFor } from '@testing-library/react'

import { scenariosApi } from '@/services/api'
import type { RegisteredScenario } from '@/types'

import { useScenarioDetail } from './useScenarioDetail'

jest.mock('@/services/api', () => ({ scenariosApi: { getScenario: jest.fn() } }))
let runtime = { ready: true, generation: 'one' }
jest.mock('./useRuntime', () => ({ useRuntime: () => runtime }))
const getScenario = jest.mocked(scenariosApi.getScenario)

const SCENARIO = { scenario_name: 'garak.api_key', supported_parameter_names: ['objective_target'] } as RegisteredScenario

describe('useScenarioDetail', () => {
  beforeEach(() => {
    getScenario.mockReset()
    runtime = { ready: true, generation: 'one' }
  })

  it('loads the scenario', async () => {
    getScenario.mockResolvedValue(SCENARIO)
    const view = renderHook(() => useScenarioDetail('garak.api_key'))

    expect(view.result.current.status).toBe('loading')
    await waitFor(() => expect(view.result.current.status).toBe('success'))
    expect(view.result.current.scenario).toBe(SCENARIO)
    expect(getScenario).toHaveBeenCalledWith('garak.api_key')
  })

  it('reports a missing scenario without an error message', async () => {
    getScenario.mockRejectedValue({ isAxiosError: true, response: { status: 404, data: { detail: 'Not found' } } })
    const view = renderHook(() => useScenarioDetail('gone'))

    await waitFor(() => expect(view.result.current.status).toBe('not-found'))
    expect(view.result.current.error).toBeNull()
  })

  it('rejects a catalog entry without input metadata', async () => {
    getScenario.mockResolvedValue({ scenario_name: 'old' } as RegisteredScenario)
    const view = renderHook(() => useScenarioDetail('old'))

    await waitFor(() => expect(view.result.current.status).toBe('error'))
    expect(view.result.current.error).toMatch(/no input support metadata/)
    expect(view.result.current.scenario).toBeNull()
  })

  it('retries after a failure', async () => {
    getScenario
      .mockRejectedValueOnce({ isAxiosError: true, response: { status: 500, data: { detail: 'boom' } } })
      .mockResolvedValueOnce(SCENARIO)
    const view = renderHook(() => useScenarioDetail('garak.api_key'))
    await waitFor(() => expect(view.result.current.error).toBe('boom'))

    act(() => view.result.current.retry())
    expect(view.result.current.status).toBe('loading')

    await waitFor(() => expect(view.result.current.status).toBe('success'))
    expect(getScenario).toHaveBeenCalledTimes(2)
  })

  it('waits for the runtime and reloads for a new generation', async () => {
    getScenario.mockResolvedValue(SCENARIO)
    runtime = { ready: false, generation: 'one' }
    const view = renderHook(() => useScenarioDetail('garak.api_key'))
    expect(getScenario).not.toHaveBeenCalled()

    runtime = { ready: true, generation: 'two' }
    view.rerender()
    await waitFor(() => expect(view.result.current.status).toBe('success'))
    runtime = { ready: true, generation: 'three' }
    view.rerender()

    await waitFor(() => expect(getScenario).toHaveBeenCalledTimes(2))
  })

  it.each(['success', 'error'] as const)(
    'ignores an older generation request that settles with %s after the new catalog',
    async (outcome: 'success' | 'error') => {
      let finishOld: () => void = () => { throw new Error('The old request has not started') }
      const refreshed = { ...SCENARIO, supported_parameter_names: ['objective_target', 'prompt'] }
      getScenario
        .mockReturnValueOnce(new Promise<RegisteredScenario>((resolve, reject) => {
          finishOld = outcome === 'success'
            ? () => resolve(SCENARIO)
            : () => reject({ isAxiosError: true, response: { status: 500, data: { detail: 'Old failure' } } })
        }))
        .mockResolvedValueOnce(refreshed)
      const view = renderHook(() => useScenarioDetail('garak.api_key'))

      runtime = { ready: true, generation: 'two' }
      view.rerender()
      await waitFor(() => expect(view.result.current.scenario).toBe(refreshed))
      expect(getScenario).toHaveBeenCalledTimes(2)

      await act(async () => { finishOld() })

      expect(view.result.current.scenario).toBe(refreshed)
      expect(view.result.current.status).toBe('success')
      expect(view.result.current.error).toBeNull()
    },
  )
})
