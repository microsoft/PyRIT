// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Tests for `WaveCompleteToast` — the bottom-right transient toast
 * that summarizes the just-completed wave per spec §2.3 / §8.1.
 *
 * Pure presentational: takes a `summary` (sourced directly from the
 * runner's `complete` WaveEvent), renders the 5-bucket "57 ✓, 3 ⚠,
 * 0 ⏱, 0 ⦾, 0 ✋" tail, and surfaces [Retry failed] / [View wave] /
 * [Dismiss] buttons. Auto-dismiss after 8 s (timeout configurable for
 * tests).
 */

import { act, fireEvent, render, screen } from '@testing-library/react'

import { WaveCompleteToast, type WaveSummary } from './WaveCompleteToast'

beforeEach(() => {
  jest.useFakeTimers()
})

afterEach(() => {
  jest.runOnlyPendingTimers()
  jest.useRealTimers()
})

const baseSummary: WaveSummary = {
  succeeded: 57,
  failed: { transient: 3, rate_limited: 0, permanent: 0 },
  blocked: 0,
  cancelled: 0,
  reflog_evicted: 0,
}

// ============================================================================
// 5-bucket display
// ============================================================================

describe('WaveCompleteToast — 5-bucket display', () => {
  it('renders the succeeded count with ✓', () => {
    const { container } = render(<WaveCompleteToast summary={baseSummary} />)
    expect(container.textContent ?? '').toMatch(/57.*\u2713/)
  })

  it('renders the failed-transient count with ⚠', () => {
    const { container } = render(<WaveCompleteToast summary={baseSummary} />)
    expect(container.textContent ?? '').toMatch(/3.*\u26A0/)
  })

  it('renders rate_limited count with ⏱', () => {
    const summary: WaveSummary = {
      ...baseSummary,
      failed: { transient: 0, rate_limited: 2, permanent: 0 },
    }
    const { container } = render(<WaveCompleteToast summary={summary} />)
    expect(container.textContent ?? '').toMatch(/2.*\u23F1/)
  })

  it('renders blocked count with ⦾', () => {
    const summary: WaveSummary = { ...baseSummary, blocked: 4 }
    const { container } = render(<WaveCompleteToast summary={summary} />)
    expect(container.textContent ?? '').toMatch(/4.*\u29BE/)
  })

  it('renders permanent failure (needs-fix) count with ✋', () => {
    const summary: WaveSummary = {
      ...baseSummary,
      failed: { transient: 0, rate_limited: 0, permanent: 1 },
    }
    const { container } = render(<WaveCompleteToast summary={summary} />)
    expect(container.textContent ?? '').toMatch(/1.*\u270B/)
  })

  it('headline reads "Wave complete:"', () => {
    const { container } = render(<WaveCompleteToast summary={baseSummary} />)
    expect((container.textContent ?? '').toLowerCase()).toContain('wave complete')
  })
})

// ============================================================================
// Buttons
// ============================================================================

describe('WaveCompleteToast — buttons', () => {
  it('renders [Retry failed] when onRetryFailed is wired AND there are transient failures', () => {
    const onRetryFailed = jest.fn()
    render(<WaveCompleteToast summary={baseSummary} onRetryFailed={onRetryFailed} />)
    const btn = screen.getByRole('button', { name: /retry failed/i })
    fireEvent.click(btn)
    expect(onRetryFailed).toHaveBeenCalledTimes(1)
  })

  it('disables [Retry failed] when every failure is rate_limited (Retry would be a no-op)', () => {
    const summary: WaveSummary = {
      ...baseSummary,
      failed: { transient: 0, rate_limited: 5, permanent: 0 },
    }
    render(<WaveCompleteToast summary={summary} onRetryFailed={jest.fn()} />)
    const btn = screen.getByRole('button', { name: /retry failed/i })
    expect(btn.hasAttribute('disabled')).toBe(true)
  })

  it('disables [Retry failed] when there are no failures at all (clean wave)', () => {
    const summary: WaveSummary = {
      ...baseSummary,
      failed: { transient: 0, rate_limited: 0, permanent: 0 },
    }
    render(<WaveCompleteToast summary={summary} onRetryFailed={jest.fn()} />)
    const btn = screen.getByRole('button', { name: /retry failed/i })
    expect(btn.hasAttribute('disabled')).toBe(true)
  })

  it('disables [Retry failed] when only permanent failures (clicking would not help)', () => {
    const summary: WaveSummary = {
      ...baseSummary,
      failed: { transient: 0, rate_limited: 0, permanent: 2 },
    }
    render(<WaveCompleteToast summary={summary} onRetryFailed={jest.fn()} />)
    expect(screen.getByRole('button', { name: /retry failed/i }).hasAttribute('disabled')).toBe(true)
  })

  it('disabled Retry carries a wait-and-refresh tooltip when failures are rate-limited (PR6.3 fix)', () => {
    const summary: WaveSummary = {
      ...baseSummary,
      failed: { transient: 0, rate_limited: 5, permanent: 0 },
    }
    render(<WaveCompleteToast summary={summary} onRetryFailed={jest.fn()} />)
    const btn = screen.getByRole('button', { name: /retry failed/i })
    expect(btn.getAttribute('title')).toMatch(/rate-limit/i)
    expect(btn.getAttribute('title')).toMatch(/refresh/i)
  })

  it('disabled Retry has no tooltip when failures are only permanent (rate-limit hint would mislead)', () => {
    const summary: WaveSummary = {
      ...baseSummary,
      failed: { transient: 0, rate_limited: 0, permanent: 2 },
    }
    render(<WaveCompleteToast summary={summary} onRetryFailed={jest.fn()} />)
    const btn = screen.getByRole('button', { name: /retry failed/i })
    expect(btn.getAttribute('title')).toBeNull()
  })

  it('hides [Retry failed] entirely when onRetryFailed is not wired', () => {
    render(<WaveCompleteToast summary={baseSummary} />)
    expect(screen.queryByRole('button', { name: /retry failed/i })).toBeNull()
  })

  it('clicking [Dismiss] fires onDismiss', () => {
    const onDismiss = jest.fn()
    render(<WaveCompleteToast summary={baseSummary} onDismiss={onDismiss} />)
    fireEvent.click(screen.getByRole('button', { name: /dismiss/i }))
    expect(onDismiss).toHaveBeenCalledTimes(1)
  })

  it('clicking [View wave] fires onViewWave', () => {
    const onViewWave = jest.fn()
    render(<WaveCompleteToast summary={baseSummary} onViewWave={onViewWave} />)
    fireEvent.click(screen.getByRole('button', { name: /view wave/i }))
    expect(onViewWave).toHaveBeenCalledTimes(1)
  })
})

// ============================================================================
// Auto-dismiss
// ============================================================================

describe('WaveCompleteToast — auto-dismiss', () => {
  it('auto-fires onDismiss after the default 8 s', () => {
    const onDismiss = jest.fn()
    render(<WaveCompleteToast summary={baseSummary} onDismiss={onDismiss} />)
    expect(onDismiss).not.toHaveBeenCalled()
    act(() => {
      jest.advanceTimersByTime(8000)
    })
    expect(onDismiss).toHaveBeenCalledTimes(1)
  })

  it('does NOT auto-fire when onDismiss is omitted', () => {
    // Smoke: nothing to assert except that no error fires + no timer is
    // installed (the component's effect cleans up).
    const { unmount } = render(<WaveCompleteToast summary={baseSummary} />)
    act(() => {
      jest.advanceTimersByTime(10000)
    })
    unmount()
  })

  it('respects the autoDismissMs override (0 disables)', () => {
    const onDismiss = jest.fn()
    render(<WaveCompleteToast summary={baseSummary} onDismiss={onDismiss} autoDismissMs={0} />)
    act(() => {
      jest.advanceTimersByTime(60000)
    })
    expect(onDismiss).not.toHaveBeenCalled()
  })

  it('cancels the timer on unmount', () => {
    const onDismiss = jest.fn()
    const { unmount } = render(
      <WaveCompleteToast summary={baseSummary} onDismiss={onDismiss} />,
    )
    unmount()
    act(() => {
      jest.advanceTimersByTime(8000)
    })
    expect(onDismiss).not.toHaveBeenCalled()
  })

  it('resets the auto-dismiss timer when summary identity changes (PR6.2 fix)', () => {
    // Without the fix, a host that re-mounts the toast with a fresh
    // summary but a memoized onDismiss reference inherits the original
    // 8-second timer — so the second wave's toast auto-dismisses
    // partway through. Test: mount, advance 6 s, swap summary, advance
    // 6 s more (12 s total). Total elapsed since the second summary
    // is 6 s, so onDismiss should NOT have fired yet. Then advance to
    // 8 s post-swap → onDismiss fires once.
    const onDismiss = jest.fn()
    const summaryA: WaveSummary = { ...baseSummary, succeeded: 1 }
    const summaryB: WaveSummary = { ...baseSummary, succeeded: 2 }
    const { rerender } = render(
      <WaveCompleteToast summary={summaryA} onDismiss={onDismiss} />,
    )
    act(() => {
      jest.advanceTimersByTime(6000)
    })
    rerender(<WaveCompleteToast summary={summaryB} onDismiss={onDismiss} />)
    // 6 s elapsed since the swap — original timer was 6 s in, would
    // have fired in another 2 s if it weren't reset.
    act(() => {
      jest.advanceTimersByTime(6000)
    })
    expect(onDismiss).not.toHaveBeenCalled()
    // 8 s since the swap → reset timer fires now.
    act(() => {
      jest.advanceTimersByTime(2000)
    })
    expect(onDismiss).toHaveBeenCalledTimes(1)
  })
})
