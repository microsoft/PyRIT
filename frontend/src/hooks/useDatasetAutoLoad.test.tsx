import { StrictMode, type ReactNode } from 'react'

import { act, renderHook } from '@testing-library/react'

import { useDatasetAutoLoad } from './useDatasetAutoLoad'

interface Props {
  scope: string
  candidates: string[]
}

describe('useDatasetAutoLoad', () => {
  const start = jest.fn()

  beforeEach(() => {
    jest.clearAllMocks()
  })

  function renderAutoLoad(initialProps: Props, strict = false) {
    return renderHook(({ scope, candidates }: Props) => useDatasetAutoLoad(scope, candidates, start), {
      initialProps,
      ...(strict ? { wrapper: ({ children }: { children: ReactNode }) => <StrictMode>{children}</StrictMode> } : {}),
    })
  }

  it('starts each candidate once, even in StrictMode and when a reload still reports it missing', () => {
    const view = renderAutoLoad({ scope: 'visit', candidates: ['a'] }, true)
    expect(start).toHaveBeenCalledTimes(1)
    expect(start).toHaveBeenLastCalledWith(['a'])
    // A catalog reload after the load can omit the dataset or still report it missing.
    view.rerender({ scope: 'visit', candidates: [] })
    view.rerender({ scope: 'visit', candidates: ['a'] })
    view.rerender({ scope: 'visit', candidates: ['a'] })
    expect(start).toHaveBeenCalledTimes(1)
  })

  it('starts a newly selected candidate while an earlier one is still tried', () => {
    const view = renderAutoLoad({ scope: 'visit', candidates: ['a'] })
    view.rerender({ scope: 'visit', candidates: ['a', 'b'] })
    expect(start.mock.calls).toEqual([[['a']], [['b']]])
  })

  it('retries only the names it is given', () => {
    const view = renderAutoLoad({ scope: 'visit', candidates: ['a', 'b'] })
    expect(start).toHaveBeenLastCalledWith(['a', 'b'])
    act(() => { view.result.current(['b']) })
    expect(start).toHaveBeenCalledTimes(2)
    expect(start).toHaveBeenLastCalledWith(['b'])
  })

  it('tries the names again in a new scope', () => {
    const view = renderAutoLoad({ scope: 'visit', candidates: ['a'] })
    view.rerender({ scope: 'next generation', candidates: ['a'] })
    expect(start.mock.calls).toEqual([[['a']], [['a']]])
  })
})
