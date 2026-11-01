import { act, renderHook, waitFor } from '@testing-library/react'

import { scenariosApi } from '@/services/api'
import { deferred } from '@/test-utils/deferred'
import type { RunScenarioRequest } from '@/types'

import { useScenarioLaunchSeed } from './useScenarioLaunchSeed'

jest.mock('@/services/api', () => ({ scenariosApi: { getLaunchConfiguration: jest.fn() } }))

const RUN_ID = '123e4567-e89b-12d3-a456-426614174000'
const NEXT_ID = '123e4567-e89b-12d3-a456-426614174001'
const REQUEST: RunScenarioRequest = { scenario_name: 'review.scan', target_name: 'saved-target', labels: { campaign: 'saved' } }

beforeEach(() => { jest.resetAllMocks() })

it('keeps saved attribution edits local without mutating the launch snapshot', async () => {
  jest.mocked(scenariosApi.getLaunchConfiguration).mockResolvedValue(REQUEST)
  const { result } = renderHook(() => useScenarioLaunchSeed(RUN_ID, 'generation', true))
  await waitFor(() => expect(result.current.request).toEqual(REQUEST))
  act(() => { result.current.updateAttribution({ labels: { campaign: 'edited' }, operation_id: NEXT_ID }) })
  expect(result.current.request?.labels).toEqual({ campaign: 'edited' })
  expect(result.current.initialRequest).toEqual(REQUEST)
  expect(REQUEST.labels).toEqual({ campaign: 'saved' })
  expect(scenariosApi.getLaunchConfiguration).toHaveBeenCalledTimes(1)
})

it('does not expose a late configuration after the selected run changes', async () => {
  const first = deferred<RunScenarioRequest>()
  const second = deferred<RunScenarioRequest>()
  jest.mocked(scenariosApi.getLaunchConfiguration).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
  const { result, rerender } = renderHook((id: string) => useScenarioLaunchSeed(id, 'generation', true), { initialProps: RUN_ID })
  rerender(NEXT_ID)
  await act(async () => { first.resolve(REQUEST) })
  expect(result.current.request).toBeUndefined()
  await act(async () => { second.resolve({ ...REQUEST, target_name: 'second-target' }) })
  expect(result.current.request?.target_name).toBe('second-target')
})

it('rejects invalid run references instead of loading ordinary defaults', () => {
  const { result } = renderHook(() => useScenarioLaunchSeed('invalid', 'generation', true))
  expect(result.current.error).toBe('The saved run reference is invalid.')
  expect(result.current.request).toBeUndefined()
  expect(scenariosApi.getLaunchConfiguration).not.toHaveBeenCalled()
})

it('does not read a saved run outside a rerun launch', () => {
  const { result } = renderHook(() => useScenarioLaunchSeed(null, 'generation', true))
  expect(result.current.active).toBe(false)
  expect(scenariosApi.getLaunchConfiguration).not.toHaveBeenCalled()
})
