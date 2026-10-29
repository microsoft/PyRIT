import { act, renderHook, waitFor } from '@testing-library/react'

import { targetsApi } from '@/services/api'
import { useRuntime } from '@/hooks/useRuntime'
import { makeTarget } from '@/test-utils/targetFixtures'

import { useTargetRegistry } from './useTargetRegistry'

jest.mock('@/services/api', () => ({
  targetsApi: { listTargets: jest.fn() },
}))
jest.mock('@/hooks/useRuntime', () => ({ useRuntime: jest.fn() }))

const listTargets = jest.mocked(targetsApi.listTargets)
const runtime = jest.mocked(useRuntime)
const first = makeTarget({ target_registry_name: 'first' })
const second = makeTarget({ target_registry_name: 'second' })

describe('useTargetRegistry', () => {
  beforeEach(() => {
    jest.resetAllMocks()
    runtime.mockReturnValue({ ready: true, state: 'ready', generation: 'first' })
  })

  it('loads every page and removes duplicate registry names', async () => {
    listTargets.mockResolvedValueOnce({
      items: [first], pagination: { limit: 200, has_more: true, next_cursor: 'next' },
    }).mockResolvedValueOnce({
      items: [first, second], pagination: { limit: 200, has_more: false },
    })
    const { result } = renderHook(useTargetRegistry)
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.targets).toEqual([first, second])
    expect(result.current.isEmpty).toBe(false)
    expect(listTargets).toHaveBeenLastCalledWith(200, 'next')
  })

  it('reports incomplete pagination and retries rather than using a partial registry', async () => {
    listTargets.mockResolvedValue({
      items: [first], pagination: { limit: 200, has_more: true, next_cursor: 'stuck' },
    })
    const { result } = renderHook(useTargetRegistry)
    await waitFor(() => expect(result.current.error).not.toBeNull())
    expect(result.current.targets).toEqual([])
    expect(result.current.isEmpty).toBe(false)
    listTargets.mockResolvedValue({
      items: [second], pagination: { limit: 200, has_more: false },
    })
    act(() => result.current.refresh())
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.error).toBeNull()
    expect(result.current.targets).toEqual([second])
  })

  it('keeps newer selections when an earlier registry load completes', async () => {
    let complete!: (response: Awaited<ReturnType<typeof targetsApi.listTargets>>) => void
    listTargets.mockReturnValueOnce(new Promise((resolve) => { complete = resolve }))
    const { result } = renderHook(useTargetRegistry)
    const updatedFirst = makeTarget({ target_registry_name: 'first', identifier_hash: 'new-hash' })
    act(() => {
      result.current.rememberTarget(updatedFirst)
      result.current.rememberTarget(second)
    })
    await act(async () => {
      complete({ items: [first], pagination: { limit: 200, has_more: false } })
    })
    expect(result.current.targets).toEqual([updatedFirst, second])

    listTargets.mockResolvedValue({ items: [], pagination: { limit: 200, has_more: false } })
    act(() => result.current.refresh())
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.targets).toEqual([])
  })

  it.each([false, true])('ignores an older request after a full reload (failed: %s)', async (failed: boolean) => {
    let finish: (() => void) | undefined
    listTargets.mockReturnValueOnce(new Promise((resolve, reject) => {
      finish = () => {
        if (failed) reject(new Error('Old failure'))
        else resolve({ items: [first], pagination: { limit: 200, has_more: false } })
      }
    }))
    const { result } = renderHook(useTargetRegistry)
    act(() => result.current.synchronizeTargets([second]))
    await act(async () => {
      if (!finish) throw new Error('Registry request did not start')
      finish()
    })
    expect(result.current.targets).toEqual([second])
    expect(result.current.loading).toBe(false)
    expect(result.current.error).toBeNull()
  })

  it('confirms an empty registry only after a complete successful load', async () => {
    let finish: ((response: Awaited<ReturnType<typeof targetsApi.listTargets>>) => void) | undefined
    listTargets.mockReturnValueOnce(new Promise((resolve) => { finish = resolve }))
    const { result } = renderHook(useTargetRegistry)
    expect(result.current.isEmpty).toBe(false)
    await act(async () => {
      if (!finish) throw new Error('Registry load did not start')
      finish({ items: [], pagination: { limit: 200, has_more: false } })
    })
    expect(result.current.isEmpty).toBe(true)
    expect(listTargets).toHaveBeenCalledTimes(1)
  })

  it('does not treat an empty first page as an empty registry', async () => {
    listTargets.mockResolvedValueOnce({
      items: [], pagination: { limit: 200, has_more: true, next_cursor: 'next' },
    }).mockResolvedValueOnce({
      items: [first], pagination: { limit: 200, has_more: false },
    })
    const { result } = renderHook(useTargetRegistry)
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.isEmpty).toBe(false)
    expect(result.current.targets).toEqual([first])
    expect(listTargets).toHaveBeenCalledTimes(2)
  })

  it('does not reuse a previous runtime generation as confirmation of an empty registry', async () => {
    listTargets.mockResolvedValueOnce({ items: [], pagination: { limit: 200, has_more: false } })
    const { result, rerender } = renderHook(useTargetRegistry)
    await waitFor(() => expect(result.current.isEmpty).toBe(true))
    let finish: ((response: Awaited<ReturnType<typeof targetsApi.listTargets>>) => void) | undefined
    listTargets.mockReturnValueOnce(new Promise((resolve) => { finish = resolve }))
    runtime.mockReturnValue({ ready: true, state: 'ready', generation: 'second' })
    rerender()
    expect(result.current.isEmpty).toBe(false)
    await act(async () => {
      if (!finish) throw new Error('New generation load did not start')
      finish({ items: [first], pagination: { limit: 200, has_more: false } })
    })
    expect(result.current.isEmpty).toBe(false)
  })

  it('does not confirm empty while refreshing or after a failed refresh', async () => {
    listTargets.mockResolvedValueOnce({ items: [], pagination: { limit: 200, has_more: false } })
    const { result } = renderHook(useTargetRegistry)
    await waitFor(() => expect(result.current.isEmpty).toBe(true))
    let fail: ((cause: Error) => void) | undefined
    listTargets.mockReturnValueOnce(new Promise((_, reject) => { fail = reject }))
    act(() => { result.current.refresh() })
    expect(result.current.isEmpty).toBe(false)
    await act(async () => {
      if (!fail) throw new Error('Registry refresh did not start')
      fail(new Error('Registry unavailable'))
    })
    expect(result.current.error).not.toBeNull()
    expect(result.current.isEmpty).toBe(false)
  })

  it('does not fetch or confirm empty when the runtime is unavailable', () => {
    runtime.mockReturnValue({ ready: false, state: 'unavailable', generation: 'first' })
    const { result } = renderHook(useTargetRegistry)
    expect(result.current.isEmpty).toBe(false)
    expect(listTargets).not.toHaveBeenCalled()
  })
})
