import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { apiClient } from '../services/api'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type ConnectionStatus = 'connected' | 'degraded' | 'disconnected'

export interface ConnectionHealth {
  /** Current connection state. */
  status: ConnectionStatus
  /** Increments each time the connection recovers from degraded/disconnected → connected. */
  reconnectCount: number
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const POLL_INTERVAL_MS = 60_000 // 60 s – normal
const DEGRADED_POLL_MS = 10_000 // 10 s – when degraded
const HEALTH_TIMEOUT_MS = 5_000 // 5 s – per health check
const FAILURES_FOR_DEGRADED = 1
const FAILURES_FOR_DISCONNECTED = 3

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const ConnectionHealthContext = createContext<ConnectionHealth>({
  status: 'connected',
  reconnectCount: 0,
})

/**
 * Access the current connection health from any descendant component.
 */
// eslint-disable-next-line react-refresh/only-export-components
export function useConnectionHealth(): ConnectionHealth {
  return useContext(ConnectionHealthContext)
}

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

export function ConnectionHealthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<ConnectionStatus>('connected')
  const [reconnectCount, setReconnectCount] = useState(0)

  const failCountRef = useRef(0)
  const prevStatusRef = useRef<ConnectionStatus>('connected')

  const checkHealth = useCallback(async () => {
    try {
      await apiClient.get('/health', { timeout: HEALTH_TIMEOUT_MS })
      const wasDown = prevStatusRef.current !== 'connected'
      failCountRef.current = 0
      setStatus('connected')
      prevStatusRef.current = 'connected'
      if (wasDown) {
        setReconnectCount((c) => c + 1)
      }
    } catch {
      failCountRef.current += 1
      if (failCountRef.current >= FAILURES_FOR_DISCONNECTED) {
        setStatus('disconnected')
        prevStatusRef.current = 'disconnected'
      } else if (failCountRef.current >= FAILURES_FOR_DEGRADED) {
        setStatus('degraded')
        prevStatusRef.current = 'degraded'
      }
    }
  }, [])

  // Periodic polling with adaptive interval
  useEffect(() => {
    const intervalMs = status === 'connected' ? POLL_INTERVAL_MS : DEGRADED_POLL_MS
    const id = setInterval(checkHealth, intervalMs)
    return () => clearInterval(id)
  }, [status, checkHealth])

  // Immediate check when tab becomes visible
  useEffect(() => {
    const handleVisibility = () => {
      if (document.visibilityState === 'visible') {
        checkHealth()
      }
    }
    document.addEventListener('visibilitychange', handleVisibility)
    return () => document.removeEventListener('visibilitychange', handleVisibility)
  }, [checkHealth])

  return (
    <ConnectionHealthContext.Provider value={{ status, reconnectCount }}>
      {children}
    </ConnectionHealthContext.Provider>
  )
}
