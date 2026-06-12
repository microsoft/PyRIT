// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Tests for `PastRunsDrawer` — the per-node "Past runs" drawer tab
 * per spec §2.3 (right-side drawer slides in when a node is selected).
 *
 * Renders the node's current `execution` (if any) + reverse-chrono
 * `executionHistory[]` reflog entries. Each entry shows attempt
 * timestamp, outcome, waveId suffix, and per-entry actions:
 *   - pin / unpin (operator-meaningful preservation; runner's
 *     `setReflogPinned` is the wire)
 *   - checkout (swap this past run back into the `execution` slot;
 *     PR6e ships the button + callback contract; the host's
 *     `makeCurrent` plumbing is V1.x)
 */

import { fireEvent, render, screen, within } from '@testing-library/react'

import { PastRunsDrawer } from './PastRunsDrawer'
import type {
  ConversationTreeNodeId,
  ExecutionRecord,
  ReflogEntry,
} from '../../runner/treeTypes'
import { nodeId } from '../../runner/testHelpers'

// ============================================================================
// Helpers
// ============================================================================

function mkExec(
  id: string,
  attemptedAt: string,
  outcome: ExecutionRecord['outcome'] = 'success',
): ExecutionRecord {
  return {
    executionId: id,
    attemptedAt,
    attackResultId: `ar-${id}`,
    conversationId: `c-${id}`,
    pieceIds: [],
    outcome,
    resolvedInputHashAtExecution: 'h',
    waveId: `wave-${id}`,
    waveTriggerKind: 'refresh_tree',
    dispatchedAt: attemptedAt,
    targetFirstByteAt: null,
    completedAt: attemptedAt,
  }
}

function mkEntry(id: string, attemptedAt: string, pinned = false): ReflogEntry {
  return { execution: mkExec(id, attemptedAt), pinned }
}

const N: ConversationTreeNodeId = nodeId('node-1')

// ============================================================================
// Empty / no execution
// ============================================================================

describe('PastRunsDrawer — empty state', () => {
  it('renders an empty-state hint when execution is null AND history is empty', () => {
    const { container } = render(
      <PastRunsDrawer nodeId={N} execution={null} executionHistory={[]} />,
    )
    expect((container.textContent ?? '').toLowerCase()).toMatch(/no past runs|no executions|nothing here/i)
  })

  it('renders nothing actionable when no callbacks are wired', () => {
    const { container } = render(
      <PastRunsDrawer nodeId={N} execution={mkExec('e1', '2026-06-11T00:00:00Z')} executionHistory={[]} />,
    )
    // No pin/unpin/checkout buttons when no callbacks supplied.
    expect(screen.queryByRole('button', { name: /pin|unpin|checkout/i })).toBeNull()
    // Current execution still rendered.
    expect((container.textContent ?? '')).toMatch(/e1/)
  })
})

// ============================================================================
// Current + history rendering
// ============================================================================

describe('PastRunsDrawer — render entries', () => {
  it('renders the current execution as the topmost entry, marked current', () => {
    const current = mkExec('e_current', '2026-06-11T12:00:00Z')
    const history: ReflogEntry[] = [
      mkEntry('e_older', '2026-06-11T11:00:00Z'),
      mkEntry('e_oldest', '2026-06-11T10:00:00Z'),
    ]
    const { container } = render(
      <PastRunsDrawer nodeId={N} execution={current} executionHistory={history} />,
    )
    const entries = container.querySelectorAll('[data-tree-reflog-entry]')
    expect(entries).toHaveLength(3)
    // First (newest) entry is the current execution.
    expect(entries[0].getAttribute('data-execution-id')).toBe('e_current')
    expect(entries[0].getAttribute('data-current')).toBe('true')
    // Subsequent entries are historical.
    expect(entries[1].getAttribute('data-execution-id')).toBe('e_older')
    expect(entries[1].getAttribute('data-current')).toBe('false')
  })

  it('renders history reverse-chronologically as supplied (host owns ordering)', () => {
    // PastRunsDrawer doesn't sort — it trusts the host's ordering so a
    // host-side filter (e.g., pinned-first) can shape the list. Test
    // pins the contract: the iteration order matches input order.
    const history: ReflogEntry[] = [
      mkEntry('e_a', '2026-06-11T11:00:00Z'),
      mkEntry('e_b', '2026-06-11T09:00:00Z'),
      mkEntry('e_c', '2026-06-11T10:00:00Z'),
    ]
    const { container } = render(
      <PastRunsDrawer nodeId={N} execution={null} executionHistory={history} />,
    )
    const entries = container.querySelectorAll('[data-tree-reflog-entry]')
    expect(entries).toHaveLength(3)
    expect([
      entries[0].getAttribute('data-execution-id'),
      entries[1].getAttribute('data-execution-id'),
      entries[2].getAttribute('data-execution-id'),
    ]).toEqual(['e_a', 'e_b', 'e_c'])
  })

  it('shows entry outcome glyph (✓ for success, ⚠ for failure)', () => {
    const history: ReflogEntry[] = [
      { execution: mkExec('e_pass', '2026-06-11T11:00:00Z', 'success'), pinned: false },
      { execution: mkExec('e_fail', '2026-06-11T10:00:00Z', 'failure'), pinned: false },
    ]
    const { container } = render(
      <PastRunsDrawer nodeId={N} execution={null} executionHistory={history} />,
    )
    const text = container.textContent ?? ''
    expect(text).toMatch(/\u2713/) // ✓
    expect(text).toMatch(/\u26A0/) // ⚠
  })
})

// ============================================================================
// Pin / unpin
// ============================================================================

describe('PastRunsDrawer — pin/unpin', () => {
  it('renders a Pin button on an unpinned entry; clicking fires onTogglePin(executionId, true)', () => {
    const onTogglePin = jest.fn()
    const history: ReflogEntry[] = [mkEntry('e_x', '2026-06-11T10:00:00Z', false)]
    const { container } = render(
      <PastRunsDrawer
        nodeId={N}
        execution={null}
        executionHistory={history}
        onTogglePin={onTogglePin}
      />,
    )
    const entry = container.querySelector('[data-execution-id="e_x"]') as HTMLElement
    fireEvent.click(within(entry).getByRole('button', { name: /^pin$/i }))
    expect(onTogglePin).toHaveBeenCalledWith('e_x', true)
  })

  it('renders an Unpin button on a pinned entry; clicking fires onTogglePin(executionId, false)', () => {
    const onTogglePin = jest.fn()
    const history: ReflogEntry[] = [mkEntry('e_x', '2026-06-11T10:00:00Z', true)]
    const { container } = render(
      <PastRunsDrawer
        nodeId={N}
        execution={null}
        executionHistory={history}
        onTogglePin={onTogglePin}
      />,
    )
    const entry = container.querySelector('[data-execution-id="e_x"]') as HTMLElement
    fireEvent.click(within(entry).getByRole('button', { name: /^unpin$/i }))
    expect(onTogglePin).toHaveBeenCalledWith('e_x', false)
  })

  it('current execution does NOT show a pin button (current is implicitly preserved)', () => {
    const current = mkExec('e_current', '2026-06-11T12:00:00Z')
    const { container } = render(
      <PastRunsDrawer
        nodeId={N}
        execution={current}
        executionHistory={[]}
        onTogglePin={jest.fn()}
      />,
    )
    const entry = container.querySelector('[data-execution-id="e_current"]') as HTMLElement
    expect(within(entry).queryByRole('button', { name: /pin|unpin/i })).toBeNull()
  })
})

// ============================================================================
// Checkout
// ============================================================================

describe('PastRunsDrawer — checkout', () => {
  it('renders a Checkout button on past entries; clicking fires onCheckout(executionId)', () => {
    const onCheckout = jest.fn()
    const history: ReflogEntry[] = [mkEntry('e_old', '2026-06-11T10:00:00Z')]
    const { container } = render(
      <PastRunsDrawer
        nodeId={N}
        execution={null}
        executionHistory={history}
        onCheckout={onCheckout}
      />,
    )
    const entry = container.querySelector('[data-execution-id="e_old"]') as HTMLElement
    fireEvent.click(within(entry).getByRole('button', { name: /checkout/i }))
    expect(onCheckout).toHaveBeenCalledWith('e_old')
  })

  it('current execution does NOT show a Checkout button (you cannot checkout to yourself)', () => {
    const current = mkExec('e_current', '2026-06-11T12:00:00Z')
    const { container } = render(
      <PastRunsDrawer
        nodeId={N}
        execution={current}
        executionHistory={[]}
        onCheckout={jest.fn()}
      />,
    )
    const entry = container.querySelector('[data-execution-id="e_current"]') as HTMLElement
    expect(within(entry).queryByRole('button', { name: /checkout/i })).toBeNull()
  })
})

// ============================================================================
// Pin marker affordance — pinned entries are visually distinguished
// ============================================================================

describe('PastRunsDrawer — pin marker', () => {
  it('pinned entries carry data-pinned="true" for downstream styling', () => {
    const history: ReflogEntry[] = [
      mkEntry('e_pin', '2026-06-11T11:00:00Z', true),
      mkEntry('e_unpin', '2026-06-11T10:00:00Z', false),
    ]
    const { container } = render(
      <PastRunsDrawer nodeId={N} execution={null} executionHistory={history} />,
    )
    expect(
      container.querySelector('[data-execution-id="e_pin"]')?.getAttribute('data-pinned'),
    ).toBe('true')
    expect(
      container.querySelector('[data-execution-id="e_unpin"]')?.getAttribute('data-pinned'),
    ).toBe('false')
  })
})

// ============================================================================
// Long executionId rendering — truncate visually, full id on title (PR6.5)
// ============================================================================

describe('PastRunsDrawer — UUID truncation', () => {
  const LONG_UUID = '7f48f2d2-3c3f-4cf8-aae5-1234567890ab'

  it('renders only a short prefix of a long executionId in the visible text', () => {
    const history: ReflogEntry[] = [mkEntry(LONG_UUID, '2026-06-11T10:00:00Z')]
    const { container } = render(
      <PastRunsDrawer nodeId={N} execution={null} executionHistory={history} />,
    )
    const entry = container.querySelector(
      `[data-execution-id="${LONG_UUID}"]`,
    ) as HTMLElement
    // The visible id span should NOT contain the full UUID string.
    const idSpan = entry.querySelector('[data-tree-execution-id-display]') as HTMLElement
    expect(idSpan).not.toBeNull()
    expect(idSpan.textContent ?? '').not.toContain(LONG_UUID)
    // It should contain the leading 8-hex prefix so the operator can
    // visually cross-reference logs.
    expect(idSpan.textContent ?? '').toContain('7f48f2d2')
  })

  it('exposes the full executionId on the id span title attribute for hover lookup', () => {
    const history: ReflogEntry[] = [mkEntry(LONG_UUID, '2026-06-11T10:00:00Z')]
    const { container } = render(
      <PastRunsDrawer nodeId={N} execution={null} executionHistory={history} />,
    )
    const idSpan = container.querySelector(
      '[data-tree-execution-id-display]',
    ) as HTMLElement
    expect(idSpan.getAttribute('title')).toBe(LONG_UUID)
  })

  it('renders short executionIds (≤12 chars) unchanged — no ellipsis added', () => {
    const history: ReflogEntry[] = [mkEntry('e_short', '2026-06-11T10:00:00Z')]
    const { container } = render(
      <PastRunsDrawer nodeId={N} execution={null} executionHistory={history} />,
    )
    const idSpan = container.querySelector(
      '[data-tree-execution-id-display]',
    ) as HTMLElement
    expect(idSpan.textContent).toBe('e_short')
    // Full id still on title for consistency.
    expect(idSpan.getAttribute('title')).toBe('e_short')
  })
})
