jest.mock('../services/api', () => ({
  apiClient: {
    get: jest.fn(),
    post: jest.fn(),
    put: jest.fn(),
    delete: jest.fn(),
    interceptors: { request: { use: jest.fn() }, response: { use: jest.fn() } },
  },
}))

import { render, screen, act, waitFor } from '@testing-library/react'
import { apiClient } from '../services/api'
import {
  ConnectionHealthProvider,
  useConnectionHealth,
} from './useConnectionHealth'

// Helper component that displays connection health state
function HealthDisplay() {
  const { status, reconnectCount } = useConnectionHealth()
  return (
    <div>
      <span data-testid="status">{status}</span>
      <span data-testid="reconnect-count">{reconnectCount}</span>
    </div>
  )
}

function renderWithProvider() {
  return render(
    <ConnectionHealthProvider>
      <HealthDisplay />
    </ConnectionHealthProvider>
  )
}

describe('useConnectionHealth', () => {
  beforeEach(() => {
    jest.useFakeTimers()
    jest.clearAllMocks()
    // Default: health check succeeds
    ;(apiClient.get as jest.Mock).mockResolvedValue({ data: { status: 'ok' } })
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  it('starts with connected status', () => {
    renderWithProvider()
    expect(screen.getByTestId('status').textContent).toBe('connected')
    expect(screen.getByTestId('reconnect-count').textContent).toBe('0')
  })

  it('transitions to degraded after 1 health check failure', async () => {
    ;(apiClient.get as jest.Mock).mockRejectedValue(new Error('Network Error'))
    renderWithProvider()

    // Trigger the first poll interval (60s)
    await act(async () => {
      jest.advanceTimersByTime(60_000)
    })

    await waitFor(() => {
      expect(screen.getByTestId('status').textContent).toBe('degraded')
    })
  })

  it('transitions to disconnected after 3 consecutive failures', async () => {
    ;(apiClient.get as jest.Mock).mockRejectedValue(new Error('Network Error'))
    renderWithProvider()

    // 1st failure at 60s → degraded
    await act(async () => {
      jest.advanceTimersByTime(60_000)
    })

    // 2nd failure at 70s (10s degraded interval)
    await act(async () => {
      jest.advanceTimersByTime(10_000)
    })

    // 3rd failure at 80s
    await act(async () => {
      jest.advanceTimersByTime(10_000)
    })

    await waitFor(() => {
      expect(screen.getByTestId('status').textContent).toBe('disconnected')
    })
  })

  it('recovers to connected and increments reconnectCount', async () => {
    // Start failing
    ;(apiClient.get as jest.Mock).mockRejectedValue(new Error('Network Error'))
    renderWithProvider()

    // Fail once → degraded
    await act(async () => {
      jest.advanceTimersByTime(60_000)
    })

    await waitFor(() => {
      expect(screen.getByTestId('status').textContent).toBe('degraded')
    })

    // Now succeed
    ;(apiClient.get as jest.Mock).mockResolvedValue({ data: { status: 'ok' } })

    await act(async () => {
      jest.advanceTimersByTime(10_000) // degraded interval
    })

    await waitFor(() => {
      expect(screen.getByTestId('status').textContent).toBe('connected')
      expect(screen.getByTestId('reconnect-count').textContent).toBe('1')
    })
  })

  it('uses 5s timeout for health checks', async () => {
    renderWithProvider()

    await act(async () => {
      jest.advanceTimersByTime(60_000)
    })

    expect(apiClient.get).toHaveBeenCalledWith('/health', { timeout: 5_000 })
  })

  it('polls at 60s when connected', async () => {
    renderWithProvider()

    await act(async () => {
      jest.advanceTimersByTime(60_000)
    })
    expect(apiClient.get).toHaveBeenCalledTimes(1)

    await act(async () => {
      jest.advanceTimersByTime(60_000)
    })
    expect(apiClient.get).toHaveBeenCalledTimes(2)
  })

  it('accelerates to 10s polling when degraded', async () => {
    ;(apiClient.get as jest.Mock).mockRejectedValue(new Error('fail'))
    renderWithProvider()

    // First poll at 60s → degraded
    await act(async () => {
      jest.advanceTimersByTime(60_000)
    })

    await waitFor(() => {
      expect(screen.getByTestId('status').textContent).toBe('degraded')
    })

    // Clear call count after degraded transition
    const callsAfterDegraded = (apiClient.get as jest.Mock).mock.calls.length

    // Next poll should be at 10s
    await act(async () => {
      jest.advanceTimersByTime(10_000)
    })

    expect((apiClient.get as jest.Mock).mock.calls.length).toBe(callsAfterDegraded + 1)
  })

  it('triggers immediate check on visibilitychange', async () => {
    renderWithProvider()

    // Simulate tab becoming visible
    Object.defineProperty(document, 'visibilityState', {
      value: 'visible',
      writable: true,
      configurable: true,
    })

    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'))
    })

    expect(apiClient.get).toHaveBeenCalledWith('/health', { timeout: 5_000 })
  })

  it('cleans up interval on unmount', async () => {
    const clearIntervalSpy = jest.spyOn(global, 'clearInterval')
    const { unmount } = renderWithProvider()

    unmount()

    expect(clearIntervalSpy).toHaveBeenCalled()
    clearIntervalSpy.mockRestore()
  })
})
