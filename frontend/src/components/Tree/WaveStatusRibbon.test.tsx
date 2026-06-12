// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Tests for `WaveStatusRibbon` — the canvas-top progress + cancel UI
 * per spec §2.3. Pure presentational; consumes the `WaveStatusState`
 * the host computes from the WaveEvent stream via `summarizeWaveEvents`.
 */

import { fireEvent, render, screen } from '@testing-library/react'

import { WaveStatusRibbon } from './WaveStatusRibbon'
import type { WaveStatusState } from './waveStatus'

// ============================================================================
// Idle — ribbon hidden
// ============================================================================

describe('WaveStatusRibbon — idle', () => {
  it('renders nothing visible when state is idle', () => {
    const { container } = render(
      <WaveStatusRibbon state={{ status: 'idle' }} />,
    )
    // The ribbon mounts an empty wrapper element so it stays in the DOM
    // for layout-stability tests, but the in-flight UI is absent.
    expect(screen.queryByRole('progressbar')).toBeNull()
    expect(screen.queryByRole('button', { name: /cancel/i })).toBeNull()
    // Sanity: the wrapper exists.
    expect(container.querySelector('[data-tree-wave-status]')).not.toBeNull()
  })
})

// ============================================================================
// Running — progress + counts + cancel
// ============================================================================

describe('WaveStatusRibbon — running', () => {
  const running: WaveStatusState = {
    status: 'running',
    waveId: 'w1',
    total: 60,
    completed: 6,
    succeeded: 3,
    failed: 1,
    queueDepth: 0,
  }

  it('shows a progressbar with completed/total values when running', () => {
    render(<WaveStatusRibbon state={running} />)
    const bar = screen.getByRole('progressbar')
    expect(bar.getAttribute('aria-valuenow')).toBe('6')
    expect(bar.getAttribute('aria-valuemax')).toBe('60')
  })

  it('shows the "completed/total" numeric counter', () => {
    const { container } = render(<WaveStatusRibbon state={running} />)
    expect(container.textContent ?? '').toMatch(/6\s*\/\s*60/)
  })

  it('shows succeeded + failed counts', () => {
    const { container } = render(<WaveStatusRibbon state={running} />)
    const text = container.textContent ?? ''
    expect(text).toMatch(/3.*\u2713/) // ✓
    expect(text).toMatch(/1.*\u26A0/) // ⚠
  })

  it('renders a Cancel button when onCancelWave is wired', () => {
    const onCancelWave = jest.fn()
    render(<WaveStatusRibbon state={running} onCancelWave={onCancelWave} />)
    const btn = screen.getByRole('button', { name: /cancel/i })
    expect(btn).not.toBeNull()
    fireEvent.click(btn)
    expect(onCancelWave).toHaveBeenCalledTimes(1)
  })

  it('hides the Cancel button when onCancelWave is missing', () => {
    render(<WaveStatusRibbon state={running} />)
    expect(screen.queryByRole('button', { name: /^cancel$/i })).toBeNull()
  })

  it('clamping: completed > total still reports the actual completed (spec is permissive)', () => {
    const over: WaveStatusState = { ...running, completed: 65, succeeded: 65, failed: 0 }
    render(<WaveStatusRibbon state={over} />)
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('65')
  })

  it('total === 0 renders a progressbar at 0 instead of dividing by zero', () => {
    // Pre-start corner case: a wave can be in `running` state with a
    // pre-computed total of 0 (e.g. an estimateRefreshCost mock that
    // returned 0 for an empty selection). The ribbon must not crash and
    // must not produce NaN on the progress bar.
    const zero: WaveStatusState = { ...running, total: 0, completed: 0 }
    render(<WaveStatusRibbon state={zero} />)
    const bar = screen.getByRole('progressbar')
    expect(bar.getAttribute('aria-valuenow')).toBe('0')
    expect(bar.getAttribute('aria-valuemax')).toBe('0')
  })
})

// ============================================================================
// Queue depth
// ============================================================================

describe('WaveStatusRibbon — queue depth', () => {
  it('does NOT render the queue chip when queueDepth is 0', () => {
    const state: WaveStatusState = {
      status: 'running',
      waveId: 'w1',
      total: 10,
      completed: 0,
      succeeded: 0,
      failed: 0,
      queueDepth: 0,
    }
    render(<WaveStatusRibbon state={state} />)
    expect(screen.queryByRole('button', { name: /cancel queued/i })).toBeNull()
    // No "queued" text-content marker either.
    expect(screen.queryByText(/queued/i)).toBeNull()
  })

  it('renders "N queued" when queueDepth > 0', () => {
    const state: WaveStatusState = {
      status: 'running',
      waveId: 'w1',
      total: 10,
      completed: 0,
      succeeded: 0,
      failed: 0,
      queueDepth: 2,
    }
    render(<WaveStatusRibbon state={state} />)
    const text = (screen.getByTestId('wave-status-queue').textContent ?? '').toLowerCase()
    expect(text).toMatch(/2/)
    expect(text).toMatch(/queued/)
  })

  it('renders [Cancel queued] when onCancelQueued is wired AND queueDepth > 0', () => {
    const onCancelQueued = jest.fn()
    const state: WaveStatusState = {
      status: 'running',
      waveId: 'w1',
      total: 10,
      completed: 0,
      succeeded: 0,
      failed: 0,
      queueDepth: 3,
    }
    render(<WaveStatusRibbon state={state} onCancelQueued={onCancelQueued} />)
    const btn = screen.getByRole('button', { name: /cancel queued/i })
    fireEvent.click(btn)
    expect(onCancelQueued).toHaveBeenCalledTimes(1)
  })

  it('does NOT render [Cancel queued] when queueDepth is 0 (even if callback wired)', () => {
    const state: WaveStatusState = {
      status: 'running',
      waveId: 'w1',
      total: 10,
      completed: 0,
      succeeded: 0,
      failed: 0,
      queueDepth: 0,
    }
    render(<WaveStatusRibbon state={state} onCancelQueued={jest.fn()} />)
    expect(screen.queryByRole('button', { name: /cancel queued/i })).toBeNull()
  })
})
