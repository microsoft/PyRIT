/**
 * Copyright (c) Microsoft Corporation.
 * Licensed under the MIT license.
 */

import { act, render, renderHook, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { ThemeProvider, resolveTheme, useTheme } from './useTheme'
import type { ThemeMode } from './useTheme'

const STORAGE_KEY = 'pyrit.themeMode'
const FORCED_COLORS_QUERY = '(forced-colors: active)'
const PREFERS_DARK_QUERY = '(prefers-color-scheme: dark)'

type MediaListener = (event: MediaQueryListEvent) => void

interface MockMediaQueryList {
  matches: boolean
  media: string
  onchange: MediaListener | null
  addListener: jest.Mock
  removeListener: jest.Mock
  addEventListener: jest.Mock
  removeEventListener: jest.Mock
  dispatchEvent: jest.Mock
}

interface MediaController {
  setMatches: (query: string, matches: boolean) => void
  trigger: (query: string, matches: boolean) => void
  reset: () => void
}

// Installs a programmable matchMedia mock for the duration of a test.
function installMatchMediaMock(): MediaController {
  const state = new Map<string, MockMediaQueryList>()
  const listeners = new Map<string, MediaListener[]>()

  function getOrCreate(query: string): MockMediaQueryList {
    let mql = state.get(query)
    if (mql) return mql
    const queryListeners: MediaListener[] = []
    listeners.set(query, queryListeners)
    mql = {
      matches: false,
      media: query,
      onchange: null,
      addListener: jest.fn(),
      removeListener: jest.fn(),
      addEventListener: jest.fn((event: string, handler: MediaListener) => {
        if (event === 'change') queryListeners.push(handler)
      }),
      removeEventListener: jest.fn((event: string, handler: MediaListener) => {
        if (event !== 'change') return
        const idx = queryListeners.indexOf(handler)
        if (idx >= 0) queryListeners.splice(idx, 1)
      }),
      dispatchEvent: jest.fn(),
    }
    state.set(query, mql)
    return mql
  }

  ;(window.matchMedia as jest.Mock).mockImplementation((query: string) => getOrCreate(query))

  return {
    setMatches(query, matches) {
      getOrCreate(query).matches = matches
    },
    trigger(query, matches) {
      const mql = getOrCreate(query)
      mql.matches = matches
      const handlers = listeners.get(query) ?? []
      const event = { matches, media: query } as MediaQueryListEvent
      act(() => {
        handlers.forEach((h) => h(event))
      })
    },
    reset() {
      state.clear()
      listeners.clear()
    },
  }
}

const wrapper = ({ children }: { children: ReactNode }) => <ThemeProvider>{children}</ThemeProvider>

describe('resolveTheme', () => {
  it('returns high-contrast whenever forced-colors is active, regardless of mode', () => {
    expect(resolveTheme('light', { forcedColors: true, prefersDark: false })).toBe('high-contrast')
    expect(resolveTheme('dark', { forcedColors: true, prefersDark: true })).toBe('high-contrast')
    expect(resolveTheme('system', { forcedColors: true, prefersDark: false })).toBe('high-contrast')
  })

  it('returns the system preference when mode is "system"', () => {
    expect(resolveTheme('system', { forcedColors: false, prefersDark: true })).toBe('dark')
    expect(resolveTheme('system', { forcedColors: false, prefersDark: false })).toBe('light')
  })

  it('returns the explicit mode when not "system" and forced-colors is off', () => {
    expect(resolveTheme('light', { forcedColors: false, prefersDark: true })).toBe('light')
    expect(resolveTheme('dark', { forcedColors: false, prefersDark: false })).toBe('dark')
  })
})

describe('useTheme / ThemeProvider', () => {
  let media: MediaController

  beforeEach(() => {
    window.localStorage.clear()
    document.documentElement.removeAttribute('data-theme')
    document.documentElement.style.removeProperty('color-scheme')
    media = installMatchMediaMock()
  })

  afterEach(() => {
    media.reset()
    // Restore the default no-match mock from setupTests.ts so subsequent
    // test files in the same worker don't pick up our custom listener wiring.
    ;(window.matchMedia as jest.Mock).mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: jest.fn(),
      removeListener: jest.fn(),
      addEventListener: jest.fn(),
      removeEventListener: jest.fn(),
      dispatchEvent: jest.fn(),
    }))
    document.documentElement.removeAttribute('data-theme')
    document.documentElement.style.removeProperty('color-scheme')
  })

  it('defaults to system mode resolving to light when no preference is stored', () => {
    const { result } = renderHook(() => useTheme(), { wrapper })
    expect(result.current.mode).toBe('system')
    expect(result.current.resolved).toBe('light')
  })

  it('returns a no-op default outside the provider', () => {
    const { result } = renderHook(() => useTheme())
    expect(result.current.mode).toBe('system')
    expect(result.current.resolved).toBe('light')
    // setMode must not throw
    expect(() => result.current.setMode('dark')).not.toThrow()
  })

  it('reads the persisted mode from localStorage on mount', () => {
    window.localStorage.setItem(STORAGE_KEY, 'dark')
    const { result } = renderHook(() => useTheme(), { wrapper })
    expect(result.current.mode).toBe('dark')
    expect(result.current.resolved).toBe('dark')
  })

  it('ignores an invalid persisted value and falls back to system', () => {
    window.localStorage.setItem(STORAGE_KEY, 'rainbow')
    const { result } = renderHook(() => useTheme(), { wrapper })
    expect(result.current.mode).toBe('system')
  })

  it('persists the mode to localStorage when setMode is called', () => {
    const { result } = renderHook(() => useTheme(), { wrapper })
    act(() => result.current.setMode('dark'))
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe('dark')
    expect(result.current.mode).toBe('dark')
  })

  it('resolves to dark when prefers-color-scheme is dark and mode is system', () => {
    media.setMatches(PREFERS_DARK_QUERY, true)
    const { result } = renderHook(() => useTheme(), { wrapper })
    expect(result.current.resolved).toBe('dark')
  })

  it('resolves to high-contrast when forced-colors is active, overriding mode', () => {
    media.setMatches(FORCED_COLORS_QUERY, true)
    window.localStorage.setItem(STORAGE_KEY, 'light')
    const { result } = renderHook(() => useTheme(), { wrapper })
    expect(result.current.mode).toBe('light')
    expect(result.current.resolved).toBe('high-contrast')
  })

  it('updates resolved theme live when prefers-color-scheme changes', () => {
    const { result } = renderHook(() => useTheme(), { wrapper })
    expect(result.current.resolved).toBe('light')
    media.trigger(PREFERS_DARK_QUERY, true)
    expect(result.current.resolved).toBe('dark')
    media.trigger(PREFERS_DARK_QUERY, false)
    expect(result.current.resolved).toBe('light')
  })

  it('updates resolved theme live when forced-colors becomes active', () => {
    const { result } = renderHook(() => useTheme(), { wrapper })
    expect(result.current.resolved).toBe('light')
    media.trigger(FORCED_COLORS_QUERY, true)
    expect(result.current.resolved).toBe('high-contrast')
    media.trigger(FORCED_COLORS_QUERY, false)
    expect(result.current.resolved).toBe('light')
  })

  it('explicit light mode wins over system dark preference', () => {
    media.setMatches(PREFERS_DARK_QUERY, true)
    const { result } = renderHook(() => useTheme(), { wrapper })
    expect(result.current.resolved).toBe('dark')
    act(() => result.current.setMode('light'))
    expect(result.current.resolved).toBe('light')
  })

  it('sets data-theme and color-scheme on documentElement to match resolved theme', () => {
    const { result } = renderHook(() => useTheme(), { wrapper })
    expect(document.documentElement.dataset.theme).toBe('light')
    expect(document.documentElement.style.colorScheme).toBe('light')

    act(() => result.current.setMode('dark'))
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(document.documentElement.style.colorScheme).toBe('dark')

    media.trigger(FORCED_COLORS_QUERY, true)
    expect(document.documentElement.dataset.theme).toBe('high-contrast')
    expect(document.documentElement.style.colorScheme).toBe('light dark')
  })

  it('survives localStorage throwing on read', () => {
    const spy = jest.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('access denied')
    })
    const { result } = renderHook(() => useTheme(), { wrapper })
    expect(result.current.mode).toBe('system')
    spy.mockRestore()
  })

  it('survives localStorage throwing on write', () => {
    const spy = jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota exceeded')
    })
    const { result } = renderHook(() => useTheme(), { wrapper })
    expect(() => act(() => result.current.setMode('dark'))).not.toThrow()
    expect(result.current.mode).toBe('dark')
    spy.mockRestore()
  })

  it('provides the same value to multiple consumers', async () => {
    const user = userEvent.setup()
    function Reader({ id }: { id: string }) {
      const { resolved, mode, setMode } = useTheme()
      return (
        <button onClick={() => setMode('dark')} data-testid={id}>
          {`${id}:${mode}:${resolved}`}
        </button>
      )
    }
    render(
      <ThemeProvider>
        <Reader id="a" />
        <Reader id="b" />
      </ThemeProvider>,
    )
    expect(screen.getByTestId('a')).toHaveTextContent('a:system:light')
    expect(screen.getByTestId('b')).toHaveTextContent('b:system:light')

    await user.click(screen.getByTestId('a'))
    expect(screen.getByTestId('a')).toHaveTextContent('a:dark:dark')
    expect(screen.getByTestId('b')).toHaveTextContent('b:dark:dark')
  })

  it('removes media-query listeners on unmount', () => {
    const { unmount } = renderHook(() => useTheme(), { wrapper })
    const forced = window.matchMedia(FORCED_COLORS_QUERY) as unknown as MockMediaQueryList
    const prefersDark = window.matchMedia(PREFERS_DARK_QUERY) as unknown as MockMediaQueryList
    unmount()
    expect(forced.removeEventListener).toHaveBeenCalledWith('change', expect.any(Function))
    expect(prefersDark.removeEventListener).toHaveBeenCalledWith('change', expect.any(Function))
  })

  it.each<[ThemeMode]>([['system'], ['light'], ['dark']])(
    'round-trips mode "%s" through localStorage',
    (mode) => {
      const { result, unmount } = renderHook(() => useTheme(), { wrapper })
      act(() => result.current.setMode(mode))
      unmount()
      const { result: result2 } = renderHook(() => useTheme(), { wrapper })
      expect(result2.current.mode).toBe(mode)
    },
  )
})
