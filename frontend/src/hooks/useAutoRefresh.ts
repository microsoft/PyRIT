import { useEffect, useEffectEvent, useRef } from 'react'

export const AUTO_REFRESH_INTERVAL_MS = 60_000
/** Longest wait, in refresh intervals, while the view's last read keeps failing. */
const MAX_AUTO_REFRESH_BACKOFF_INTERVALS = 16

/**
 * Revalidates a visible view without owning its data or initial load.
 * While `failing` reports that the view's last read failed, each retry doubles the wait,
 * up to MAX_AUTO_REFRESH_BACKOFF_INTERVALS, so an overloaded backend is not polled at full rate.
 */
export function useAutoRefresh(
  refresh: () => void | Promise<void>,
  enabled: boolean = true,
  failing: boolean = false,
): void {
  const onRefresh = useEffectEvent(refresh)
  const isFailing = useEffectEvent((): boolean => failing)
  const running = useRef(false)
  const failedRetries = useRef(0)

  useEffect(() => {
    if (!enabled) return
    let lastCheck = performance.now()
    let intervalsSinceCheck = 0
    const intervalsDue = (): number => (isFailing()
      ? Math.min(2 ** failedRetries.current, MAX_AUTO_REFRESH_BACKOFF_INTERVALS) : 1)
    const check = async (): Promise<void> => {
      if (document.visibilityState === 'hidden' || running.current) return
      running.current = true
      lastCheck = performance.now()
      intervalsSinceCheck = 0
      failedRetries.current = isFailing() ? failedRetries.current + 1 : 0
      try {
        await onRefresh()
      } finally {
        running.current = false
      }
    }
    const onInterval = (): void => {
      intervalsSinceCheck += 1
      if (intervalsSinceCheck >= intervalsDue()) void check()
    }
    let timer = window.setInterval(onInterval, AUTO_REFRESH_INTERVAL_MS)
    const onVisibilityChange = (): void => {
      if (document.visibilityState === 'hidden'
        || performance.now() - lastCheck < AUTO_REFRESH_INTERVAL_MS * intervalsDue()) return
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
