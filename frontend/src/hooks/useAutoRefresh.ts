import { useEffect, useEffectEvent, useRef } from 'react'

export const AUTO_REFRESH_INTERVAL_MS = 60_000

/** Revalidates a visible view without owning its data or initial load. */
export function useAutoRefresh(refresh: () => void | Promise<void>, enabled: boolean = true): void {
  const onRefresh = useEffectEvent(refresh)
  const running = useRef(false)

  useEffect(() => {
    if (!enabled) return
    let lastCheck = performance.now()
    const check = async (): Promise<void> => {
      if (document.visibilityState === 'hidden' || running.current) return
      running.current = true
      lastCheck = performance.now()
      try {
        await onRefresh()
      } finally {
        running.current = false
      }
    }
    const onInterval = (): void => { void check() }
    let timer = window.setInterval(onInterval, AUTO_REFRESH_INTERVAL_MS)
    const onVisibilityChange = (): void => {
      if (document.visibilityState === 'hidden'
        || performance.now() - lastCheck < AUTO_REFRESH_INTERVAL_MS) return
      window.clearInterval(timer)
      timer = window.setInterval(onInterval, AUTO_REFRESH_INTERVAL_MS)
      void check()
    }
    document.addEventListener('visibilitychange', onVisibilityChange)
    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [enabled])
}
