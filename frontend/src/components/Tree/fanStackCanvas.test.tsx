// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Tests for the Fan-Children Stack render flow through TreeCanvas +
 * FanCard.
 *
 * Covers:
 *   - FanCard renders the stack summary body when data.stackedSummary
 *     is present (kind × count + status line)
 *   - FanCard renders a ⊞/⊟ toggle button when StackCollapseContext is
 *     provided; the button invokes toggleStack with the fan's nodeId
 *   - TreeCanvas auto-collapses stackable fans with N > 3 by default
 *     (the fan's children are dropped from the DOM)
 *   - TreeCanvas does NOT auto-collapse stackable fans with N ≤ 3
 *   - Clicking the toggle inside an auto-collapsed fan expands it
 *     (children reappear)
 */

import { fireEvent, render } from '@testing-library/react'
import { ReactFlowProvider } from '@xyflow/react'

import { FanCard } from './nodeCards'
import { StackCollapseContext, type StackCollapseValue } from './stackCollapseContext'
import { TreeCanvas } from './TreeCanvas'
import {
  mkFan,
  mkRoot,
  mkSend,
  mkTree,
  mkUserTurn,
  nodeId,
} from '../../runner/testHelpers'
import type { FanNode, FanVariant } from '../../runner/treeTypes'

function attemptVariants(n: number): FanVariant[] {
  return Array.from({ length: n }, () => ({ axis: 'attempt' as const, payload: {} }))
}

// FanCard direct-mount harness: synthesize NodeProps with optional
// stackedSummary and optional StackCollapseContext value.
type FanNodeData = {
  node: FanNode
  stackedSummary?: import('./fanStack').StackAggregate
}

function renderFanCard({
  fanNode,
  stackedSummary,
  stackContext = null,
  selected = false,
}: {
  fanNode: FanNode
  stackedSummary?: import('./fanStack').StackAggregate
  stackContext?: StackCollapseValue | null
  selected?: boolean
}) {
  const data: FanNodeData = { node: fanNode, stackedSummary }
  const props = {
    id: fanNode.id as string,
    data,
    selected,
  } as unknown as Parameters<typeof FanCard>[0]
  return render(
    <StackCollapseContext.Provider value={stackContext}>
      <ReactFlowProvider>
        <FanCard {...props} />
      </ReactFlowProvider>
    </StackCollapseContext.Provider>,
  )
}

// ============================================================================
// FanCard — stack summary body
// ============================================================================

describe('FanCard — stack summary body', () => {
  it('renders the stack summary body when data.stackedSummary is present', () => {
    const fan = mkFan('f', 'parent', {
      axis: 'attempt',
      variants: attemptVariants(5),
    })
    const { container } = renderFanCard({
      fanNode: fan,
      stackedSummary: {
        childKind: 'send',
        total: 5,
        byState: { clean: 4, edited: 0, stale: 0, running: 0, failed: 1, cancelled: 0, draft: 0 },
      },
    })
    const summary = container.querySelector('[data-tree-stack-summary]')
    expect(summary).not.toBeNull()
    // Kind × total line
    expect(summary?.textContent).toMatch(/send.*×\s*5/i)
    // Status line: 4 ✓, 1 ⚠
    expect(summary?.textContent).toMatch(/4\s*✓/)
    expect(summary?.textContent).toMatch(/1\s*⚠/)
  })

  it('renders the running count in the status line', () => {
    const fan = mkFan('f', 'parent', { axis: 'attempt', variants: attemptVariants(3) })
    const { container } = renderFanCard({
      fanNode: fan,
      stackedSummary: {
        childKind: 'send',
        total: 3,
        byState: { clean: 1, edited: 0, stale: 0, running: 2, failed: 0, cancelled: 0, draft: 0 },
      },
    })
    const summary = container.querySelector('[data-tree-stack-summary]')
    expect(summary?.textContent).toMatch(/2\s*●/)
  })

  it('renders an em-dash when no children are in a counted state (all stale-only is "pending")', () => {
    const fan = mkFan('f', 'parent', { axis: 'attempt', variants: attemptVariants(2) })
    const { container } = renderFanCard({
      fanNode: fan,
      stackedSummary: {
        childKind: 'send',
        total: 2,
        byState: { clean: 0, edited: 0, stale: 0, running: 0, failed: 0, cancelled: 0, draft: 0 },
      },
    })
    const summary = container.querySelector('[data-tree-stack-summary]')
    expect(summary?.textContent).toMatch(/—/)
  })

  it('does NOT render the summary body when data.stackedSummary is undefined', () => {
    const fan = mkFan('f', 'parent', { axis: 'attempt', variants: attemptVariants(3) })
    const { container } = renderFanCard({ fanNode: fan })
    expect(container.querySelector('[data-tree-stack-summary]')).toBeNull()
  })
})

// ============================================================================
// FanCard — stack toggle button
// ============================================================================

describe('FanCard — stack toggle', () => {
  it('renders a toggle button when StackCollapseContext is provided', () => {
    const fan = mkFan('f', 'parent', { axis: 'attempt', variants: attemptVariants(3) })
    const ctx: StackCollapseValue = {
      collapsedFanIds: new Set(),
      toggleStack: jest.fn(),
    }
    const { container } = renderFanCard({ fanNode: fan, stackContext: ctx })
    expect(container.querySelector('[data-tree-stack-toggle]')).not.toBeNull()
  })

  it('does NOT render the toggle when StackCollapseContext is null', () => {
    const fan = mkFan('f', 'parent', { axis: 'attempt', variants: attemptVariants(3) })
    const { container } = renderFanCard({ fanNode: fan, stackContext: null })
    expect(container.querySelector('[data-tree-stack-toggle]')).toBeNull()
  })

  it('clicking the toggle invokes ctx.toggleStack with the fan id', () => {
    const toggleStack = jest.fn()
    const fan = mkFan('f', 'parent', { axis: 'attempt', variants: attemptVariants(3) })
    const { container } = renderFanCard({
      fanNode: fan,
      stackContext: { collapsedFanIds: new Set(), toggleStack },
    })
    const btn = container.querySelector('[data-tree-stack-toggle] button')!
    fireEvent.click(btn)
    expect(toggleStack).toHaveBeenCalledTimes(1)
    expect(toggleStack).toHaveBeenCalledWith(nodeId('f'))
  })

  it('toggle aria-label says "Collapse to stack" when not collapsed (stackedSummary absent)', () => {
    const fan = mkFan('f', 'parent', { axis: 'attempt', variants: attemptVariants(3) })
    const { container } = renderFanCard({
      fanNode: fan,
      stackContext: { collapsedFanIds: new Set(), toggleStack: jest.fn() },
    })
    const btn = container.querySelector('[data-tree-stack-toggle] button')!
    expect(btn.getAttribute('aria-label')).toMatch(/collapse/i)
  })

  it('toggle aria-label says "Expand stack" when collapsed (stackedSummary present)', () => {
    const fan = mkFan('f', 'parent', { axis: 'attempt', variants: attemptVariants(3) })
    const { container } = renderFanCard({
      fanNode: fan,
      stackedSummary: {
        childKind: 'send',
        total: 3,
        byState: { clean: 3, edited: 0, stale: 0, running: 0, failed: 0, cancelled: 0, draft: 0 },
      },
      stackContext: { collapsedFanIds: new Set([nodeId('f')]), toggleStack: jest.fn() },
    })
    const btn = container.querySelector('[data-tree-stack-toggle] button')!
    expect(btn.getAttribute('aria-label')).toMatch(/expand/i)
  })
})

// ============================================================================
// TreeCanvas — auto-collapse + toggle round-trip
// ============================================================================

describe('TreeCanvas — Fan-Children Stack auto-collapse', () => {
  it('auto-collapses a stackable attempt-fan with N > 3 (children hidden)', () => {
    const sends = Array.from({ length: 5 }, (_, i) => mkSend(`s_${i}`, 'f'))
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f', 'u', { axis: 'attempt', variants: attemptVariants(5) }),
      ...sends,
    ])
    const { container } = render(<TreeCanvas tree={tree} />)
    // The fan card is rendered.
    const fanWrapper = container.querySelector('[data-tree-node-id="f"][data-selected]')
    expect(fanWrapper).not.toBeNull()
    // The stack summary body is present (children are collapsed).
    expect(fanWrapper?.querySelector('[data-tree-stack-summary]')).not.toBeNull()
    // The send children are NOT rendered as separate cards.
    for (let i = 0; i < 5; i++) {
      expect(container.querySelector(`[data-tree-node-id="s_${i}"]`)).toBeNull()
    }
  })

  it('does NOT auto-collapse a stackable attempt-fan with N = 3 (expanded by default)', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f', 'u', { axis: 'attempt', variants: attemptVariants(3) }),
      mkSend('s_0', 'f'),
      mkSend('s_1', 'f'),
      mkSend('s_2', 'f'),
    ])
    const { container } = render(<TreeCanvas tree={tree} />)
    // Stack summary NOT rendered (fan is expanded).
    expect(container.querySelector('[data-tree-stack-summary]')).toBeNull()
    // Each child Send is rendered as its own card.
    expect(container.querySelector('[data-tree-node-id="s_0"]')).not.toBeNull()
    expect(container.querySelector('[data-tree-node-id="s_1"]')).not.toBeNull()
    expect(container.querySelector('[data-tree-node-id="s_2"]')).not.toBeNull()
  })

  it('does NOT auto-collapse a converter-axis fan (not stackable)', () => {
    const variants: FanVariant[] = Array.from({ length: 5 }, () => ({
      axis: 'converter' as const,
      payload: { converters: [] },
    }))
    const sends = Array.from({ length: 5 }, (_, i) => mkSend(`s_${i}`, 'f'))
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f', 'u', { axis: 'converter', variants }),
      ...sends,
    ])
    const { container } = render(<TreeCanvas tree={tree} />)
    // Converter-axis: not stackable, children visible.
    expect(container.querySelector('[data-tree-stack-summary]')).toBeNull()
    expect(container.querySelector('[data-tree-node-id="s_0"]')).not.toBeNull()
  })

  it('clicking the toggle on an auto-collapsed fan expands it (children reappear)', () => {
    const sends = Array.from({ length: 5 }, (_, i) => mkSend(`s_${i}`, 'f'))
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f', 'u', { axis: 'attempt', variants: attemptVariants(5) }),
      ...sends,
    ])
    const { container } = render(<TreeCanvas tree={tree} />)
    // Pre-toggle: children are collapsed.
    expect(container.querySelector('[data-tree-node-id="s_0"]')).toBeNull()
    // Click the toggle inside the fan card.
    const fanWrapper = container.querySelector('[data-tree-node-id="f"][data-selected]')!
    const toggleBtn = fanWrapper.querySelector('[data-tree-stack-toggle] button')!
    fireEvent.click(toggleBtn)
    // Post-toggle: children reappear.
    expect(container.querySelector('[data-tree-node-id="s_0"]')).not.toBeNull()
    expect(container.querySelector('[data-tree-stack-summary]')).toBeNull()
  })

  it('clicking the toggle on an expanded fan collapses it', () => {
    // 3-child fan: expanded by default. Click toggle → collapsed.
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f', 'u', { axis: 'attempt', variants: attemptVariants(3) }),
      mkSend('s_0', 'f'),
      mkSend('s_1', 'f'),
      mkSend('s_2', 'f'),
    ])
    const { container } = render(<TreeCanvas tree={tree} />)
    expect(container.querySelector('[data-tree-node-id="s_0"]')).not.toBeNull()
    const fanWrapper = container.querySelector('[data-tree-node-id="f"][data-selected]')!
    const toggleBtn = fanWrapper.querySelector('[data-tree-stack-toggle] button')!
    fireEvent.click(toggleBtn)
    // Children now hidden; summary visible.
    expect(container.querySelector('[data-tree-node-id="s_0"]')).toBeNull()
    expect(container.querySelector('[data-tree-stack-summary]')).not.toBeNull()
  })

  it('re-keys collapse state when tree.id changes (swap to a different tree)', () => {
    const sends1 = Array.from({ length: 5 }, (_, i) => mkSend(`s_${i}`, 'f'))
    const tree1 = mkTree(
      'r',
      [
        mkRoot('r'),
        mkUserTurn('u', 'r'),
        mkFan('f', 'u', { axis: 'attempt', variants: attemptVariants(5) }),
        ...sends1,
      ],
      { id: 't-1' },
    )
    // Different tree: no stackable fan at all.
    const tree2 = mkTree('r2', [mkRoot('r2'), mkUserTurn('u2', 'r2')], { id: 't-2' })
    const { container, rerender } = render(<TreeCanvas tree={tree1} />)
    expect(container.querySelector('[data-tree-stack-summary]')).not.toBeNull()
    rerender(<TreeCanvas tree={tree2} />)
    expect(container.querySelector('[data-tree-stack-summary]')).toBeNull()
    expect(container.querySelector('[data-tree-node-id="r2"]')).not.toBeNull()
  })
})
