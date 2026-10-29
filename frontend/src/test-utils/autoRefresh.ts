import { act } from '@testing-library/react'
import { afterEach, beforeEach, jest } from '@jest/globals'

import { AUTO_REFRESH_INTERVAL_MS } from '@/hooks/useAutoRefresh'

export function installAutoRefreshTimers(): void {
  beforeEach(() => {
    jest.spyOn(window, 'setInterval')
    jest.spyOn(window, 'clearInterval')
  })
  afterEach(() => { jest.restoreAllMocks() })
}

export async function advanceAutoRefresh(): Promise<void> {
  jest.spyOn(Date, 'now').mockReturnValue(Date.now() + AUTO_REFRESH_INTERVAL_MS)
  const scheduled = jest.mocked(window.setInterval)
  const cleared = jest.mocked(window.clearInterval)
  await act(async () => {
    for (const [index, call] of scheduled.mock.calls.entries()) {
      if (call[1] !== AUTO_REFRESH_INTERVAL_MS || typeof call[0] !== 'function') continue
      const id: unknown = scheduled.mock.results[index].value
      if (!cleared.mock.calls.some((entry) => entry[0] === id)) call[0](...call.slice(2))
    }
  })
}
