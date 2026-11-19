import { useCallback, useMemo, useSyncExternalStore } from 'react'

export function useMediaQuery(query: string, onChange?: () => void): boolean {
  const mediaQuery = useMemo(() => (
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia(query)
      : undefined
  ), [query])
  const subscribe = useCallback((notify: () => void): (() => void) => {
    if (!mediaQuery) return () => {}
    const handleChange = (): void => {
      onChange?.()
      notify()
    }
    mediaQuery.addEventListener('change', handleChange)
    return () => { mediaQuery.removeEventListener('change', handleChange) }
  }, [mediaQuery, onChange])

  return useSyncExternalStore(subscribe, () => mediaQuery?.matches ?? false, () => false)
}
