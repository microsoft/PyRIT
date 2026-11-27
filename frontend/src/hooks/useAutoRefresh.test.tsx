import { act, render, screen } from '@testing-library/react'

import { AUTO_REFRESH_INTERVAL_MS, useAutoRefresh } from './useAutoRefresh'

interface RefreshViewProps {
  readonly refresh: () => void | Promise<void>
  readonly enabled?: boolean
  readonly failing?: boolean
}

function RefreshView({ refresh, enabled = true, failing = false }: RefreshViewProps) {
  useAutoRefresh(refresh, enabled, failing)
  return <input aria-label="Draft" defaultValue="Keep this" />
}

async function advanceIntervals(count: number): Promise<void> {
  await act(async () => { await jest.advanceTimersByTimeAsync(AUTO_REFRESH_INTERVAL_MS * count) })
}

describe('useAutoRefresh', () => {
  beforeEach(() => { jest.useFakeTimers() })
  afterEach(() => { jest.useRealTimers(); jest.restoreAllMocks() })

  it('should double the wait after each failed read and resume the normal interval after a success', async () => {
    const refresh = jest.fn()
    const rendered = render(<RefreshView refresh={refresh} failing />)
    await advanceIntervals(1)
    expect(refresh).toHaveBeenCalledTimes(1)
    await advanceIntervals(1)
    expect(refresh).toHaveBeenCalledTimes(1)
    await advanceIntervals(1)
    expect(refresh).toHaveBeenCalledTimes(2)
    await advanceIntervals(3)
    expect(refresh).toHaveBeenCalledTimes(2)
    await advanceIntervals(1)
    expect(refresh).toHaveBeenCalledTimes(3)
    rendered.rerender(<RefreshView refresh={refresh} />)
    await advanceIntervals(2)
    expect(refresh).toHaveBeenCalledTimes(5)
  })

  it('should cap the failure backoff at sixteen intervals', async () => {
    const refresh = jest.fn()
    render(<RefreshView refresh={refresh} failing />)
    // Retries after 1, 2, 4, 8, 16, then 16 and 16 more intervals.
    await advanceIntervals(62)
    expect(refresh).toHaveBeenCalledTimes(6)
    await advanceIntervals(1)
    expect(refresh).toHaveBeenCalledTimes(7)
  })

  it('should wait the backoff when a failing view becomes visible again', async () => {
    const refresh = jest.fn()
    const visibility = jest.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
    render(<RefreshView refresh={refresh} failing />)
    await advanceIntervals(1)
    expect(refresh).toHaveBeenCalledTimes(1)
    visibility.mockReturnValue('hidden')
    await advanceIntervals(1)
    visibility.mockReturnValue('visible')
    await act(async () => { document.dispatchEvent(new Event('visibilitychange')) })
    expect(refresh).toHaveBeenCalledTimes(1)
    visibility.mockReturnValue('hidden')
    await advanceIntervals(1)
    visibility.mockReturnValue('visible')
    await act(async () => { document.dispatchEvent(new Event('visibilitychange')) })
    expect(refresh).toHaveBeenCalledTimes(2)
  })

  it('should wait a full minute and keep the view mounted', async () => {
    const refresh = jest.fn()
    render(<RefreshView refresh={refresh} />)
    const draft = screen.getByRole('textbox', { name: 'Draft' })
    draft.focus()
    await act(async () => { jest.advanceTimersByTime(AUTO_REFRESH_INTERVAL_MS - 1) })
    expect(refresh).not.toHaveBeenCalled()
    await act(async () => { jest.advanceTimersByTime(1) })
    expect(refresh).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('textbox', { name: 'Draft' })).toBe(draft)
    expect(draft).toHaveFocus()
    expect(draft).toHaveValue('Keep this')
  })

  it('should skip hidden views and make one due check when visible again', async () => {
    const refresh = jest.fn()
    const visibility = jest.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')
    render(<RefreshView refresh={refresh} />)
    await act(async () => { jest.advanceTimersByTime(AUTO_REFRESH_INTERVAL_MS * 3) })
    expect(refresh).not.toHaveBeenCalled()
    visibility.mockReturnValue('visible')
    await act(async () => { document.dispatchEvent(new Event('visibilitychange')) })
    expect(refresh).toHaveBeenCalledTimes(1)
    await act(async () => { document.dispatchEvent(new Event('visibilitychange')) })
    expect(refresh).toHaveBeenCalledTimes(1)
  })

  it('should use the latest callback without restarting the minute', async () => {
    const first = jest.fn()
    const next = jest.fn()
    const rendered = render(<RefreshView refresh={first} />)
    await act(async () => { jest.advanceTimersByTime(AUTO_REFRESH_INTERVAL_MS / 2) })
    rendered.rerender(<RefreshView refresh={next} />)
    await act(async () => { jest.advanceTimersByTime(AUTO_REFRESH_INTERVAL_MS / 2) })
    expect(first).not.toHaveBeenCalled()
    expect(next).toHaveBeenCalledTimes(1)
  })

  it('should not skip interval ticks after a late tick or a wall-clock correction', async () => {
    let clock = 0
    jest.spyOn(performance, 'now').mockImplementation(() => clock)
    jest.spyOn(Date, 'now').mockImplementation(() => clock)
    const refresh = jest.fn()
    render(<RefreshView refresh={refresh} />)
    clock = AUTO_REFRESH_INTERVAL_MS + 5
    await act(async () => { await jest.advanceTimersByTimeAsync(AUTO_REFRESH_INTERVAL_MS) })
    expect(refresh).toHaveBeenCalledTimes(1)
    clock = AUTO_REFRESH_INTERVAL_MS * 2
    await act(async () => { await jest.advanceTimersByTimeAsync(AUTO_REFRESH_INTERVAL_MS) })
    expect(refresh).toHaveBeenCalledTimes(2)
    jest.spyOn(Date, 'now').mockReturnValue(-1_000_000)
    clock += AUTO_REFRESH_INTERVAL_MS
    await act(async () => { await jest.advanceTimersByTimeAsync(AUTO_REFRESH_INTERVAL_MS) })
    expect(refresh).toHaveBeenCalledTimes(3)
  })

  it('should wait a full minute after a due visibility read before the next interval read', async () => {
    const visibility = jest.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')
    const refresh = jest.fn()
    render(<RefreshView refresh={refresh} />)
    await act(async () => { await jest.advanceTimersByTimeAsync(65_000) })
    visibility.mockReturnValue('visible')
    await act(async () => { document.dispatchEvent(new Event('visibilitychange')) })
    expect(refresh).toHaveBeenCalledTimes(1)
    await act(async () => { await jest.advanceTimersByTimeAsync(59_999) })
    expect(refresh).toHaveBeenCalledTimes(1)
    await act(async () => { await jest.advanceTimersByTimeAsync(1) })
    expect(refresh).toHaveBeenCalledTimes(2)
  })

  it('should retain the in-flight guard when scheduling is disabled and enabled again', async () => {
    let finish: () => void = () => {}
    const refresh = jest.fn(() => new Promise<void>((resolve) => { finish = resolve }))
    const rendered = render(<RefreshView refresh={refresh} />)
    await act(async () => { await jest.advanceTimersByTimeAsync(AUTO_REFRESH_INTERVAL_MS) })
    rendered.rerender(<RefreshView refresh={refresh} enabled={false} />)
    rendered.rerender(<RefreshView refresh={refresh} />)
    await act(async () => { await jest.advanceTimersByTimeAsync(AUTO_REFRESH_INTERVAL_MS) })
    expect(refresh).toHaveBeenCalledTimes(1)
    await act(async () => { finish() })
    await act(async () => { await jest.advanceTimersByTimeAsync(AUTO_REFRESH_INTERVAL_MS) })
    expect(refresh).toHaveBeenCalledTimes(2)
  })

  it('should not overlap an unfinished read and should stop after unmount', async () => {
    let finish: () => void = () => {}
    const refresh = jest.fn(() => new Promise<void>((resolve) => { finish = resolve }))
    const rendered = render(<RefreshView refresh={refresh} />)
    await act(async () => { jest.advanceTimersByTime(AUTO_REFRESH_INTERVAL_MS) })
    await act(async () => { jest.advanceTimersByTime(AUTO_REFRESH_INTERVAL_MS * 2) })
    expect(refresh).toHaveBeenCalledTimes(1)
    await act(async () => { finish() })
    rendered.unmount()
    await act(async () => { jest.advanceTimersByTime(AUTO_REFRESH_INTERVAL_MS) })
    expect(refresh).toHaveBeenCalledTimes(1)
  })

  it('should not schedule a disabled view', async () => {
    const refresh = jest.fn()
    render(<RefreshView refresh={refresh} enabled={false} />)
    await act(async () => { jest.advanceTimersByTime(AUTO_REFRESH_INTERVAL_MS * 2) })
    expect(refresh).not.toHaveBeenCalled()
  })
})
