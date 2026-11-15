import { act } from '@testing-library/react'
import { afterEach, beforeEach, jest } from '@jest/globals'

import { AUTO_REFRESH_INTERVAL_MS } from '@/hooks/useAutoRefresh'

export function installAutoRefreshTimers(): void {
  beforeEach(() => { jest.useFakeTimers({ advanceTimers: true }) })
  afterEach(() => { jest.useRealTimers(); jest.restoreAllMocks() })
}

export async function advanceAutoRefresh(): Promise<void> {
  await act(async () => { await jest.advanceTimersByTimeAsync(AUTO_REFRESH_INTERVAL_MS) })
}
