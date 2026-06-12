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
})
