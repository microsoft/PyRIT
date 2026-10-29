import { useEffect, useEffectEvent } from 'react'

export const AUTO_REFRESH_INTERVAL_MS = 60_000

/** Revalidates a visible view without owning its data or initial load. */
export function useAutoRefresh(refresh: () => void | Promise<void>, enabled: boolean = true): void {
  const onRefresh = useEffectEvent(refresh)

  useEffect(() => {
    if (!enabled) return
    let running = false
    let lastCheck = Date.now()
    const check = async (): Promise<void> => {
      if (document.visibilityState === 'hidden' || running
        || Date.now() - lastCheck < AUTO_REFRESH_INTERVAL_MS) return
      running = true
      lastCheck = Date.now()
      try {
        await onRefresh()
      } finally {
        running = false
      }
    }
    const onVisibilityChange = (): void => { void check() }
    const timer = window.setInterval(onVisibilityChange, AUTO_REFRESH_INTERVAL_MS)
    document.addEventListener('visibilitychange', onVisibilityChange)
    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [enabled])
}
