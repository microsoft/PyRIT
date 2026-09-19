import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react'
import type { ReactNode } from 'react'
import {
  FluentProvider,
  createHighContrastTheme,
} from '@fluentui/react-components'

import { isThemeMode, THEME_PRESETS } from '@/themes/themePresets'
import type { ResolvedTheme, ThemeContextValue, ThemeMode, ThemePreset } from '@/types'

import { useThemeProviderStyles } from './ThemeProvider.styles'

export type { ResolvedTheme, ThemeContextValue, ThemeMode } from '@/types'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const STORAGE_KEY = 'pyrit.themeMode'
const FORCED_COLORS_QUERY = '(forced-colors: active)'
const PREFERS_DARK_QUERY = '(prefers-color-scheme: dark)'

// Build the high-contrast theme once at module load — `createHighContrastTheme`
// returns a 459-key object and is purely derived from defaults.
const HIGH_CONTRAST_THEME = createHighContrastTheme()

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function readStoredMode(): ThemeMode {
  if (typeof window === 'undefined') return 'system'
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    return isThemeMode(raw) ? raw : 'system'
  } catch {
    return 'system'
  }
}

function persistMode(mode: ThemeMode): void {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(STORAGE_KEY, mode)
  } catch {
    /* localStorage may be unavailable (private mode, quota, sandboxed iframe). */
  }
}

function safeMatchMedia(query: string): MediaQueryList | null {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return null
  }
  try {
    return window.matchMedia(query)
  } catch {
    return null
  }
}

interface SystemSignals {
  forcedColors: boolean
  prefersDark: boolean
}

function readSystemSignals(): SystemSignals {
  return {
    forcedColors: safeMatchMedia(FORCED_COLORS_QUERY)?.matches ?? false,
    prefersDark: safeMatchMedia(PREFERS_DARK_QUERY)?.matches ?? false,
  }
}

/**
 * Resolve the user preference + system signals to the theme we will actually
 * render. Forced-colors always wins: if Windows / browser High Contrast is on,
 * picking any non-HC theme produces a broken-looking UI because the browser
 * repaints with system colors anyway.
 */
// eslint-disable-next-line react-refresh/only-export-components
export function resolveTheme(mode: ThemeMode, signals: SystemSignals): ResolvedTheme {
  if (signals.forcedColors) return 'high-contrast'
  if (mode === 'system') return signals.prefersDark ? 'dark' : 'light'
  return THEME_PRESETS[mode].resolved
}

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const DEFAULT_CONTEXT: ThemeContextValue = {
  mode: 'system',
  resolved: 'light',
  setMode: () => {},
}

const ThemeContext = createContext<ThemeContextValue>(DEFAULT_CONTEXT)

/** Access the current theme state. Returns a no-op fallback outside the provider. */
// eslint-disable-next-line react-refresh/only-export-components
export function useTheme(): ThemeContextValue {
  return useContext(ThemeContext)
}

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

export function ThemeProvider({ children }: { children: ReactNode }) {
  const styles = useThemeProviderStyles()
  // Lazy initializer reads from localStorage exactly once, so the first paint
  // is correct (no flash of wrong theme) and StrictMode double-render is safe.
  const [mode, setModeState] = useState<ThemeMode>(() => readStoredMode())
  const [signals, setSignals] = useState<SystemSignals>(() => readSystemSignals())

  const setMode = useCallback((next: ThemeMode) => {
    setModeState(next)
    persistMode(next)
  }, [])

  // Subscribe to OS-level signals. Both `forced-colors` and
  // `prefers-color-scheme` can change at runtime (Windows HC toggle, macOS
  // dark-mode schedule, etc.). We listen to both and re-read on any change.
  useEffect(() => {
    const forced = safeMatchMedia(FORCED_COLORS_QUERY)
    const prefersDark = safeMatchMedia(PREFERS_DARK_QUERY)

    if (!forced && !prefersDark) return

    const handleChange = () => setSignals(readSystemSignals())

    // Sync once on mount in case signals changed between initial render and
    // effect (e.g., a slow first paint).
    handleChange()

    forced?.addEventListener('change', handleChange)
    prefersDark?.addEventListener('change', handleChange)

    return () => {
      forced?.removeEventListener('change', handleChange)
      prefersDark?.removeEventListener('change', handleChange)
    }
  }, [])

  const resolved = useMemo(() => resolveTheme(mode, signals), [mode, signals])
  const preset: ThemePreset | undefined = resolved === 'high-contrast'
    ? undefined
    : THEME_PRESETS[mode === 'system' ? resolved : mode]
  const background = preset?.background

  // Apply theme-related attributes to <html> so non-Fluent CSS (native
  // scrollbars, form controls, anything in global.css) follows the theme.
  useEffect(() => {
    if (typeof document === 'undefined') return
    const root = document.documentElement
    root.dataset.theme = resolved
    // `light dark` for HC lets the browser pick whichever matches the active
    // forced-colors palette; native widgets are already overridden by the OS.
    root.style.colorScheme = resolved === 'high-contrast' ? 'light dark' : resolved
  }, [resolved])

  const value = useMemo<ThemeContextValue>(
    () => ({ mode, resolved, background, setMode }),
    [mode, resolved, background, setMode],
  )

  return (
    <ThemeContext.Provider value={value}>
      <FluentProvider
        theme={preset?.theme ?? HIGH_CONTRAST_THEME}
        className={background ? styles.decorated : undefined}
      >
        {children}
      </FluentProvider>
    </ThemeContext.Provider>
  )
}
