// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Tests for `useCostGuardrailModal` — the React-state-backed
 * `CostGuardrail` provider. Owns the modal's pending-decision state
 * and the per-session suppression flag.
 *
 * Per spec §8.1: waves below `confirmThresholdCount` skip the modal
 * entirely (one-click); waves at or above threshold prompt. "Don't ask
 * again this session" suppresses subsequent at-threshold prompts; the
 * 2× safety floor forces the modal back regardless of suppression.
 */

import { act, fireEvent, render, renderHook, screen } from '@testing-library/react'
import { useState } from 'react'

import { useCostGuardrailModal } from './useCostGuardrailModal'
import type { WaveTriggerKind } from '../../runner/treeTypes'

// ============================================================================
// Helpers
// ============================================================================

/**
 * Renders a real DOM mount of the hook so the modal's portal actually
 * appears under document.body — Fluent's Dialog primitive portals out,
 * and renderHook alone gives us the hook's return value but no DOM tree
 * for the modal to attach to.
 */
function mountHook(opts: Parameters<typeof useCostGuardrailModal>[0]) {
  let latest: ReturnType<typeof useCostGuardrailModal> | null = null
  function Harness() {
    latest = useCostGuardrailModal(opts)
    return <>{latest.modalElement}</>
  }
  render(<Harness />)
  if (latest === null) throw new Error('Harness did not render')
  return {
    get current() {
      if (latest === null) throw new Error('hook unmounted')
      return latest
    },
  }
}

// ============================================================================
// Below threshold — one-click
// ============================================================================

describe('useCostGuardrailModal — below threshold', () => {
  it('approve() resolves true synchronously when count < threshold (no modal)', async () => {
    const { result } = renderHook(() =>
      useCostGuardrailModal({ confirmThresholdCount: 20 }),
    )
    const approved = await result.current.guardrail.approve(5, 'refresh_tree')
    expect(approved).toBe(true)
    expect(result.current.modalElement).toBeNull()
  })

  it('approve() resolves true when count exactly equals threshold - 1', async () => {
    const { result } = renderHook(() =>
      useCostGuardrailModal({ confirmThresholdCount: 20 }),
    )
    const approved = await result.current.guardrail.approve(19, 'refresh_tree')
    expect(approved).toBe(true)
  })
})

// ============================================================================
// At/above threshold — modal shows
// ============================================================================

describe('useCostGuardrailModal — at/above threshold', () => {
  it('approve() at threshold renders the modal and stays pending', async () => {
    const h = mountHook({ confirmThresholdCount: 20 })
    let resolved: boolean | null = null
    await act(async () => {
      void h.current.guardrail.approve(20, 'refresh_tree').then((v) => {
        resolved = v
      })
      await Promise.resolve()
    })
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(resolved).toBeNull()
  })

  it('modal shows the estimated call count', async () => {
    const h = mountHook({ confirmThresholdCount: 20 })
    await act(async () => {
      void h.current.guardrail.approve(60, 'refresh_tree')
      await Promise.resolve()
    })
    const dialog = screen.getByRole('dialog')
    expect(dialog.textContent).toMatch(/60/)
  })

  it('modal shows the threshold value for operator context', async () => {
    const h = mountHook({ confirmThresholdCount: 20 })
    await act(async () => {
      void h.current.guardrail.approve(60, 'refresh_tree')
      await Promise.resolve()
    })
    expect(screen.getByRole('dialog').textContent).toMatch(/20/)
  })

  it('modal body uses a gerund clause for waveTriggerKind=refresh_tree', async () => {
    const h = mountHook({ confirmThresholdCount: 20 })
    await act(async () => {
      void h.current.guardrail.approve(60, 'refresh_tree')
      await Promise.resolve()
    })
    expect(screen.getByRole('dialog').textContent).toMatch(/Refreshing the tree/)
  })

  it.each([
    ['refresh_tree', /Refreshing the tree/],
    ['refresh_node', /Refreshing this node/],
    ['refresh_subtree', /Refreshing this subtree/],
    ['retry_failed', /Retrying failed nodes/],
    ['synced_peer_add', /Adding a synced peer/],
    ['cross_tree_rebase', /Performing a cross-tree rebase/],
  ] as Array<[WaveTriggerKind, RegExp]>)(
    'modal body uses a gerund clause for %s',
    async (kind, pattern) => {
      const h = mountHook({ confirmThresholdCount: 20 })
      await act(async () => {
        void h.current.guardrail.approve(60, kind)
        await Promise.resolve()
      })
      expect(screen.getByRole('dialog').textContent).toMatch(pattern)
    },
  )

  it.each([
    ['refresh_tree', /Refresh tree \(60 calls\)/],
    ['refresh_node', /Refresh node \(60 calls\)/],
    ['refresh_subtree', /Refresh subtree \(60 calls\)/],
    ['retry_failed', /Retry failed \(60 calls\)/],
    ['synced_peer_add', /Add synced peer \(60 calls\)/],
    ['cross_tree_rebase', /Cross-tree rebase \(60 calls\)/],
  ] as Array<[WaveTriggerKind, RegExp]>)(
    'modal title for %s',
    async (kind, pattern) => {
      const h = mountHook({ confirmThresholdCount: 20 })
      await act(async () => {
        void h.current.guardrail.approve(60, kind)
        await Promise.resolve()
      })
      expect(screen.getByRole('dialog').textContent).toMatch(pattern)
    },
  )

  it('uses singular "call" when count === 1 (title and body)', async () => {
    const h = mountHook({ confirmThresholdCount: 1 })
    await act(async () => {
      void h.current.guardrail.approve(1, 'refresh_tree')
      await Promise.resolve()
    })
    const body = screen.getByRole('dialog').textContent ?? ''
    // Title: "Refresh tree (1 call)?" — no plural s.
    expect(body).toMatch(/\(1 call\)/)
    // Body: "will send 1 call to the target" — no plural s.
    expect(body).toMatch(/send 1 call to the target/)
  })

  it('uses singular "call" for threshold when threshold === 1', async () => {
    const h = mountHook({ confirmThresholdCount: 1 })
    await act(async () => {
      void h.current.guardrail.approve(5, 'refresh_tree')
      await Promise.resolve()
    })
    const body = screen.getByRole('dialog').textContent ?? ''
    expect(body).toMatch(/threshold: 1 call per wave/)
  })

  it('modal body uses "per wave" (not "per refresh") for the threshold qualifier', async () => {
    const h = mountHook({ confirmThresholdCount: 20 })
    await act(async () => {
      void h.current.guardrail.approve(60, 'retry_failed')
      await Promise.resolve()
    })
    const body = screen.getByRole('dialog').textContent ?? ''
    expect(body).toMatch(/per wave/)
    expect(body).not.toMatch(/per refresh/)
  })
})

// ============================================================================
// Dialog onOpenChange (ESC / overlay dismiss) — routes through onCancel
// ============================================================================

describe('useCostGuardrailModal — ESC / overlay dismiss', () => {
  it('pressing Escape on the dialog resolves approve() to false (via onOpenChange)', async () => {
    const h = mountHook({ confirmThresholdCount: 20 })
    let resolved: boolean | null = null
    let promise: Promise<void>
    await act(async () => {
      promise = h.current.guardrail.approve(60, 'refresh_tree').then((v) => {
        resolved = v
      })
      await Promise.resolve()
    })
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    await act(async () => {
      await promise
    })
    expect(resolved).toBe(false)
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})

// ============================================================================
// Confirm / Cancel
// ============================================================================

describe('useCostGuardrailModal — confirm and cancel', () => {
  it('clicking [Refresh] resolves approve() to true and dismisses the modal', async () => {
    const h = mountHook({ confirmThresholdCount: 20 })
    let resolved: boolean | null = null
    let promise: Promise<void>
    await act(async () => {
      promise = h.current.guardrail
        .approve(60, 'refresh_tree')
        .then((v) => {
          resolved = v
        })
      await Promise.resolve()
    })
    fireEvent.click(screen.getByRole('button', { name: /^refresh$/i }))
    await act(async () => {
      await promise
    })
    expect(resolved).toBe(true)
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('clicking [Cancel] resolves approve() to false and dismisses', async () => {
    const h = mountHook({ confirmThresholdCount: 20 })
    let resolved: boolean | null = null
    let promise: Promise<void>
    await act(async () => {
      promise = h.current.guardrail
        .approve(60, 'refresh_tree')
        .then((v) => {
          resolved = v
        })
      await Promise.resolve()
    })
    fireEvent.click(screen.getByRole('button', { name: /^cancel$/i }))
    await act(async () => {
      await promise
    })
    expect(resolved).toBe(false)
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})

// ============================================================================
// "Don't ask again this session" suppression
// ============================================================================

describe('useCostGuardrailModal — suppression', () => {
  it('checkbox + Refresh suppresses subsequent at-threshold approvals', async () => {
    const h = mountHook({ confirmThresholdCount: 20 })
    // First call uses count=25 (above threshold 20, below safety floor 40)
    // so suppression actually takes effect on the second call.
    let firstResolved: boolean | null = null
    let first: Promise<void>
    await act(async () => {
      first = h.current.guardrail
        .approve(25, 'refresh_tree')
        .then((v) => {
          firstResolved = v
        })
      await Promise.resolve()
    })
    fireEvent.click(screen.getByRole('checkbox', { name: /don't ask again/i }))
    fireEvent.click(screen.getByRole('button', { name: /^refresh$/i }))
    await act(async () => {
      await first
    })
    expect(firstResolved).toBe(true)

    // Second at-threshold call (count=25, below safety floor 40) should
    // auto-approve without showing the modal.
    const second = await h.current.guardrail.approve(25, 'refresh_tree')
    expect(second).toBe(true)
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('suppression does NOT survive a Cancel — operator stays prompted', async () => {
    const h = mountHook({ confirmThresholdCount: 20 })
    let first: Promise<boolean>
    await act(async () => {
      first = h.current.guardrail.approve(25, 'refresh_tree')
      await Promise.resolve()
    })
    fireEvent.click(screen.getByRole('checkbox', { name: /don't ask again/i }))
    fireEvent.click(screen.getByRole('button', { name: /^cancel$/i }))
    await act(async () => {
      await first
    })

    // Second at-threshold call should still prompt — Cancel doesn't commit
    // the suppression because the operator never agreed to proceed.
    let secondResolved: boolean | null = null
    await act(async () => {
      void h.current.guardrail.approve(25, 'refresh_tree').then((v) => {
        secondResolved = v
      })
      await Promise.resolve()
    })
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(secondResolved).toBeNull()
  })
})

// ============================================================================
// 2× safety floor — modal fires even when suppressed
// ============================================================================

describe('useCostGuardrailModal — 2× safety floor', () => {
  it('waves at or above 2× threshold ALWAYS prompt, even when suppression is set', async () => {
    const h = mountHook({ confirmThresholdCount: 20 })
    // Suppress via a normal at-threshold confirm.
    let first: Promise<boolean>
    await act(async () => {
      first = h.current.guardrail.approve(25, 'refresh_tree')
      await Promise.resolve()
    })
    fireEvent.click(screen.getByRole('checkbox', { name: /don't ask again/i }))
    fireEvent.click(screen.getByRole('button', { name: /^refresh$/i }))
    await act(async () => {
      await first
    })

    // 40 is the 2× safety floor (2 * 20). Should re-prompt.
    let safetyResolved: boolean | null = null
    await act(async () => {
      void h.current.guardrail.approve(40, 'refresh_tree').then((v) => {
        safetyResolved = v
      })
      await Promise.resolve()
    })
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(safetyResolved).toBeNull()
  })

  it('waves at the threshold but below 2× honor suppression', async () => {
    const h = mountHook({ confirmThresholdCount: 20 })
    let first: Promise<boolean>
    await act(async () => {
      first = h.current.guardrail.approve(25, 'refresh_tree')
      await Promise.resolve()
    })
    fireEvent.click(screen.getByRole('checkbox', { name: /don't ask again/i }))
    fireEvent.click(screen.getByRole('button', { name: /^refresh$/i }))
    await act(async () => {
      await first
    })

    // 39 is below 2× (40); suppression applies.
    const second = await h.current.guardrail.approve(39, 'refresh_tree')
    expect(second).toBe(true)
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})

// ============================================================================
// Concurrent approve() — sync-reject the second caller (PR6.4)
// ============================================================================

describe('useCostGuardrailModal — concurrent approve guard', () => {
  let consoleErrorSpy: jest.SpyInstance
  beforeEach(() => {
    consoleErrorSpy = jest.spyOn(console, 'error').mockImplementation(() => {})
  })
  afterEach(() => {
    consoleErrorSpy.mockRestore()
  })

  it('approve() resolves false synchronously and logs error when a decision is already pending', async () => {
    const h = mountHook({ confirmThresholdCount: 20 })

    let firstResolved: boolean | null = null
    await act(async () => {
      void h.current.guardrail.approve(60, 'refresh_tree').then((v) => {
        firstResolved = v
      })
      await Promise.resolve()
    })
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(firstResolved).toBeNull()

    // Second concurrent approve() — must reject without overwriting the
    // first's pending resolver. The Promise resolves on the very next
    // microtask, which a single await Promise.resolve() flushes.
    let secondResolved: boolean | null = null
    await act(async () => {
      void h.current.guardrail.approve(80, 'refresh_node').then((v) => {
        secondResolved = v
      })
      await Promise.resolve()
    })
    expect(secondResolved).toBe(false)
    expect(consoleErrorSpy).toHaveBeenCalled()

    // First decision must still be pending — modal still rendered, its
    // promise unresolved.
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(firstResolved).toBeNull()
    // Modal text still reflects the FIRST call (count=60, refresh_tree)
    // — proving the resolver wasn't overwritten.
    expect(screen.getByRole('dialog').textContent).toMatch(/60/)
    expect(screen.getByRole('dialog').textContent).toMatch(/refresh tree/i)
  })

  it('after a rejected concurrent call, the FIRST approve still resolves on Refresh click', async () => {
    const h = mountHook({ confirmThresholdCount: 20 })

    let firstResolved: boolean | null = null
    let firstPromise: Promise<void>
    await act(async () => {
      firstPromise = h.current.guardrail
        .approve(60, 'refresh_tree')
        .then((v) => {
          firstResolved = v
        })
      await Promise.resolve()
    })

    await act(async () => {
      void h.current.guardrail.approve(80, 'refresh_node')
      await Promise.resolve()
    })

    fireEvent.click(screen.getByRole('button', { name: /^refresh$/i }))
    await act(async () => {
      await firstPromise
    })
    expect(firstResolved).toBe(true)
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})

// ============================================================================
// Stability under re-render — guardrail identity
// ============================================================================

describe('useCostGuardrailModal — referential stability', () => {
  it('guardrail object identity is stable across re-renders', () => {
    let renderCount = 0
    function Harness() {
      const [, setN] = useState(0)
      const { guardrail } = useCostGuardrailModal({ confirmThresholdCount: 20 })
      renderCount += 1
      ;(Harness as unknown as { latest: typeof guardrail }).latest = guardrail
      ;(Harness as unknown as { bump: () => void }).bump = () => setN((n) => n + 1)
      return null
    }
    render(<Harness />)
    const before = (Harness as unknown as { latest: unknown }).latest
    act(() => {
      ;(Harness as unknown as { bump: () => void }).bump()
    })
    const after = (Harness as unknown as { latest: unknown }).latest
    expect(after).toBe(before)
    expect(renderCount).toBeGreaterThanOrEqual(2)
  })
})

// ============================================================================
// Controlled suppression (sourced from WorkspaceSettings) — PR6a rewire
// ============================================================================

describe('useCostGuardrailModal — controlled suppression', () => {
  it('honors a persisted suppressed=true on mount (at-threshold approve auto-approves, no modal)', async () => {
    // count=25 is at/above threshold 20 but below the 2x safety floor (40),
    // so persisted suppression should auto-approve without showing the modal.
    const h = mountHook({ confirmThresholdCount: 20, suppressed: true })
    const approved = await h.current.guardrail.approve(25, 'refresh_tree')
    expect(approved).toBe(true)
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('the 2x safety floor overrides persisted suppression (modal still shows)', async () => {
    const h = mountHook({ confirmThresholdCount: 20, suppressed: true })
    let resolved: boolean | null = null
    await act(async () => {
      void h.current.guardrail.approve(40, 'refresh_tree').then((v) => {
        resolved = v
      })
      await Promise.resolve()
    })
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(resolved).toBeNull()
  })

  it('checkbox + Refresh notifies onChangeSuppressed(true)', async () => {
    const onChangeSuppressed = jest.fn()
    const h = mountHook({ confirmThresholdCount: 20, onChangeSuppressed })
    await act(async () => {
      void h.current.guardrail.approve(25, 'refresh_tree')
      await Promise.resolve()
    })
    fireEvent.click(screen.getByRole('checkbox', { name: /don't ask again/i }))
    fireEvent.click(screen.getByRole('button', { name: /^refresh$/i }))
    expect(onChangeSuppressed).toHaveBeenCalledWith(true)
  })

  it('Refresh WITHOUT the checkbox does not notify onChangeSuppressed', async () => {
    const onChangeSuppressed = jest.fn()
    const h = mountHook({ confirmThresholdCount: 20, onChangeSuppressed })
    await act(async () => {
      void h.current.guardrail.approve(25, 'refresh_tree')
      await Promise.resolve()
    })
    fireEvent.click(screen.getByRole('button', { name: /^refresh$/i }))
    expect(onChangeSuppressed).not.toHaveBeenCalled()
  })

  it('a later suppressed=true prop (persisted mid-session) is honored by subsequent approvals', async () => {
    // Drives the controlled prop from a parent so a change to the persisted
    // setting takes effect without remounting the hook.
    function Harness({ suppressed }: { suppressed: boolean }) {
      const r = useCostGuardrailModal({ confirmThresholdCount: 20, suppressed })
      ;(Harness as unknown as { latest: typeof r }).latest = r
      return <>{r.modalElement}</>
    }
    const view = render(<Harness suppressed={false} />)
    view.rerender(<Harness suppressed={true} />)
    const r = (Harness as unknown as { latest: ReturnType<typeof useCostGuardrailModal> }).latest
    const approved = await r.guardrail.approve(25, 'refresh_tree')
    expect(approved).toBe(true)
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})
