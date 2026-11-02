import { act, renderHook } from '@testing-library/react'

import { NARROW_SCREEN_QUERY } from '@/styles/touchTargets'

import { useMediaQuery } from './useMediaQuery'

describe('useMediaQuery', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('updates matches and notifies transitions only while subscribed', () => {
    const mediaQuery = Object.assign(new EventTarget(), {
      media: NARROW_SCREEN_QUERY,
      matches: false,
      onchange: null,
      addListener: jest.fn(),
      removeListener: jest.fn(),
    })
    jest.mocked(window.matchMedia).mockReturnValue(mediaQuery)
    const onChange = jest.fn()
    const { result, unmount } = renderHook(() => useMediaQuery(NARROW_SCREEN_QUERY, onChange))
    expect(result.current).toBe(false)
    expect(onChange).not.toHaveBeenCalled()

    act(() => {
      mediaQuery.matches = true
      mediaQuery.dispatchEvent(new Event('change'))
    })
    expect(result.current).toBe(true)
    act(() => {
      mediaQuery.matches = false
      mediaQuery.dispatchEvent(new Event('change'))
    })
    expect(result.current).toBe(false)
    expect(onChange).toHaveBeenCalledTimes(2)

    unmount()
    mediaQuery.dispatchEvent(new Event('change'))
    expect(onChange).toHaveBeenCalledTimes(2)
  })
})
