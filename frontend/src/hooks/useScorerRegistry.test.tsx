import { act, renderHook, waitFor } from '@testing-library/react'

import * as runtime from '@/hooks/useRuntime'
import { listRegisteredScorers } from '@/services/scorerRegistry'
import type { ScorerInstance } from '@/types'

import { useScorerRegistry } from './useScorerRegistry'

jest.mock('@/services/scorerRegistry', () => ({ listRegisteredScorers: jest.fn() }))
const list = jest.mocked(listRegisteredScorers)
const scorer: ScorerInstance = { scorer_registry_name: 'judge', identifier: { class_name: 'Judge', hash: 'hash' } }

describe('useScorerRegistry', () => {
  beforeEach(() => { list.mockReset() })
  afterEach(() => { jest.restoreAllMocks() })

  it('ignores an obsolete catalog after runtime reinitialization', async () => {
    let resolveOld: (value: ScorerInstance[]) => void = () => { throw new Error('Old request not started') }
    list.mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve }))
      .mockResolvedValueOnce([scorer])
    const readiness = jest.spyOn(runtime, 'useRuntime').mockReturnValue({ ready: true, state: 'ready', generation: 'old' })
    const view = renderHook(() => useScorerRegistry())
    readiness.mockReturnValue({ ready: true, state: 'ready', generation: 'new' })
    view.rerender()
    await waitFor(() => expect(view.result.current.scorers).toEqual([scorer]))
    await act(async () => { resolveOld([]) })
    expect(view.result.current.scorers).toEqual([scorer])
    expect(view.result.current.loading).toBe(false)
  })

  it('reports load errors and supports an explicit refresh', async () => {
    list.mockRejectedValueOnce(new Error('Catalog unavailable')).mockResolvedValueOnce([scorer])
    const view = renderHook(() => useScorerRegistry())
    await waitFor(() => expect(view.result.current.error).toBe('Catalog unavailable'))
    act(() => view.result.current.refresh())
    await waitFor(() => expect(view.result.current.scorers).toEqual([scorer]))
    expect(view.result.current.error).toBeNull()
  })
})
