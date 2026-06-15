// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Tests for the per-node ActionRail component + its wiring through
 * TreeCanvas → cards.
 *
 * Scope (PR5c): the common-to-every-node rail (Refresh / Branch /
 * Delete / Focus-in-path-chat). Kind-specific
 * actions (✏ edit, ⚡ converter, ≡ role, ↻×N re-run, etc.) defer to
 * later sub-PRs — each needs its own state machine + dialog.
 *
 * Pinned contracts:
 *   - rail renders one button per visible action
 *   - clicking a button invokes the matching callback with the node id
 *   - future-only actions (Branch-subtree) do not render in normal V1.0
 *   - rail visibility ties to `[data-selected="true"]` OR `:hover`
 *     (CSS-level; tested via the data attributes the cards already
 *     emit, not via simulated hover events — jsdom's hover doesn't
 *     fire :hover pseudo-class)
 *   - missing callbacks (undefined) silently disable the affordance
 *     (operator can mount TreeCanvas without wiring every action)
 *   - tooltips render the action label on hover-focus
 */

import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { TreeCanvas } from './TreeCanvas'
import type { ActionCallbacks } from './actionRail'
import { ActionRail } from './actionRail'
import {
  mkRoot,
  mkSend,
  mkTree,
  mkUserTurn,
  nodeId,
} from '../../runner/testHelpers'

// ============================================================================
// 1. ActionRail in isolation
// ============================================================================

describe('ActionRail — isolated render', () => {
  it('renders Refresh / Branch / Delete / Open buttons when callbacks supplied', () => {
    const callbacks: ActionCallbacks = {
      onRefresh: jest.fn(),
      onBranch: jest.fn(),
      onDelete: jest.fn(),
      onOpenLinear: jest.fn(),
    }
    render(<ActionRail nodeId={nodeId('r')} callbacks={callbacks} branchLabel="Branch from here" />)
    expect(screen.getByRole('button', { name: /refresh/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /branch from here/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /branch as subtree/i })).toBeNull()
    expect(screen.getByRole('button', { name: /delete/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /focus in path chat/i })).toBeInTheDocument()
  })

  it('uses the supplied branchLabel ("Clone tree" on root, "Branch from here" elsewhere)', () => {
    const callbacks: ActionCallbacks = {
      onRefresh: jest.fn(),
      onBranch: jest.fn(),
    }
    const { rerender } = render(
      <ActionRail nodeId={nodeId('r')} callbacks={callbacks} branchLabel="Clone tree" />,
    )
    expect(screen.getByRole('button', { name: /clone tree/i })).toBeInTheDocument()
    rerender(
      <ActionRail nodeId={nodeId('r')} callbacks={callbacks} branchLabel="Branch from here" />,
    )
    expect(screen.getByRole('button', { name: /branch from here/i })).toBeInTheDocument()
  })

  it('Branch-subtree future action is hidden in normal V1.0', () => {
    const callbacks: ActionCallbacks = { onRefresh: jest.fn(), onBranch: jest.fn() }
    render(<ActionRail nodeId={nodeId('r')} callbacks={callbacks} branchLabel="Clone tree" />)
    expect(screen.queryByRole('button', { name: /branch as subtree/i })).toBeNull()
  })

  it('clicking Refresh invokes onRefresh(nodeId)', async () => {
    const onRefresh = jest.fn()
    const callbacks: ActionCallbacks = { onRefresh, onBranch: jest.fn() }
    render(<ActionRail nodeId={nodeId('r')} callbacks={callbacks} branchLabel="x" />)
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: /refresh/i }))
    expect(onRefresh).toHaveBeenCalledTimes(1)
    expect(onRefresh).toHaveBeenCalledWith(nodeId('r'))
  })

  it('clicking Branch invokes onBranch(nodeId)', async () => {
    const onBranch = jest.fn()
    const callbacks: ActionCallbacks = { onRefresh: jest.fn(), onBranch }
    render(<ActionRail nodeId={nodeId('r')} callbacks={callbacks} branchLabel="Clone tree" />)
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: /clone tree/i }))
    expect(onBranch).toHaveBeenCalledWith(nodeId('r'))
  })

  it('clicking Delete invokes onDelete(nodeId)', async () => {
    const onDelete = jest.fn()
    const callbacks: ActionCallbacks = {
      onRefresh: jest.fn(),
      onBranch: jest.fn(),
      onDelete,
    }
    render(<ActionRail nodeId={nodeId('r')} callbacks={callbacks} branchLabel="x" />)
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: /delete/i }))
    expect(onDelete).toHaveBeenCalledWith(nodeId('r'))
  })

  it('clicking Focus-in-path-chat invokes onOpenLinear(nodeId)', async () => {
    const onOpenLinear = jest.fn()
    const callbacks: ActionCallbacks = {
      onRefresh: jest.fn(),
      onBranch: jest.fn(),
      onOpenLinear,
    }
    render(<ActionRail nodeId={nodeId('r')} callbacks={callbacks} branchLabel="x" />)
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: /focus in path chat/i }))
    expect(onOpenLinear).toHaveBeenCalledWith(nodeId('r'))
  })

  it('omits Delete button when onDelete callback is undefined', () => {
    const callbacks: ActionCallbacks = {
      onRefresh: jest.fn(),
      onBranch: jest.fn(),
    }
    render(<ActionRail nodeId={nodeId('r')} callbacks={callbacks} branchLabel="x" />)
    expect(screen.queryByRole('button', { name: /delete/i })).toBeNull()
  })

  it('omits Focus-in-path-chat button when onOpenLinear is undefined', () => {
    const callbacks: ActionCallbacks = {
      onRefresh: jest.fn(),
      onBranch: jest.fn(),
    }
    render(<ActionRail nodeId={nodeId('r')} callbacks={callbacks} branchLabel="x" />)
    expect(screen.queryByRole('button', { name: /focus in path chat/i })).toBeNull()
  })

  it('hides Branch button when onBranch is undefined (no clone affordance)', () => {
    const callbacks: ActionCallbacks = { onRefresh: jest.fn() }
    render(<ActionRail nodeId={nodeId('r')} callbacks={callbacks} branchLabel="Clone tree" />)
    expect(screen.queryByRole('button', { name: /clone tree/i })).toBeNull()
  })

  it('hides Refresh button when onRefresh is undefined', () => {
    const callbacks: ActionCallbacks = {}
    render(<ActionRail nodeId={nodeId('r')} callbacks={callbacks} branchLabel="x" />)
    expect(screen.queryByRole('button', { name: /refresh/i })).toBeNull()
  })

  it('renders nothing when all callbacks are undefined (empty rail)', () => {
    const callbacks: ActionCallbacks = {}
    const { container } = render(
      <ActionRail nodeId={nodeId('r')} callbacks={callbacks} branchLabel="x" />,
    )
    // Branch-subtree is the only always-rendered (disabled) slot. Verify
    // the rail wrapper itself still renders so PR5d's edge `+` chip has
    // anchor positioning; but no functional buttons are present.
    expect(screen.queryByRole('button', { name: /refresh|clone|branch from here|delete|focus in path chat/i })).toBeNull()
    expect(container.querySelector('[data-tree-action-rail]')).not.toBeNull()
  })
})

// ============================================================================
// 2. ActionRail wired through TreeCanvas → cards
// ============================================================================

describe('TreeCanvas — action callbacks wiring', () => {
  it('renders a rail on every card when callbacks are supplied at the TreeCanvas boundary', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkSend('s', 'u'),
    ])
    const callbacks: ActionCallbacks = {
      onRefresh: jest.fn(),
      onBranch: jest.fn(),
      onDelete: jest.fn(),
      onOpenLinear: jest.fn(),
    }
    const { container } = render(<TreeCanvas tree={tree} actionCallbacks={callbacks} />)
    const rails = container.querySelectorAll('[data-tree-action-rail]')
    expect(rails).toHaveLength(3)
  })

  it("clicking a card's Refresh button invokes onRefresh with that card's nodeId", async () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkSend('s', 'u'),
    ])
    const onRefresh = jest.fn()
    const callbacks: ActionCallbacks = {
      onRefresh,
      onBranch: jest.fn(),
    }
    const { container } = render(<TreeCanvas tree={tree} actionCallbacks={callbacks} />)
    // Find the Send card's Refresh button via its DOM-scoped query.
    const sendCard = container.querySelector('[data-tree-node-id="s"]')
    expect(sendCard).not.toBeNull()
    const refreshButtons = sendCard!.querySelectorAll('button')
    const refreshBtn = Array.from(refreshButtons).find((b) =>
      b.getAttribute('aria-label')?.match(/refresh/i),
    )
    expect(refreshBtn).toBeDefined()
    // userEvent.click() trips react-flow's pointerdown handler inside
    // jsdom (the canvas's pointer-event tracking dereferences a null
    // window owner). fireEvent.click() dispatches a single MouseEvent
    // that bypasses react-flow's pointer interception while still
    // triggering the Fluent Button's onClick.
    fireEvent.click(refreshBtn!)
    expect(onRefresh).toHaveBeenCalledTimes(1)
    expect(onRefresh).toHaveBeenCalledWith(nodeId('s'))
  })

  it('TreeCanvas renders cards WITHOUT rails when actionCallbacks prop is omitted', () => {
    // Backwards-compat: PR5a/PR5b TreeCanvas use is `<TreeCanvas tree={...} />`
    // with no callbacks. The rail must opt in; an undefined callbacks prop
    // means "no actions wired" and the rail is suppressed entirely.
    const tree = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r')])
    const { container } = render(<TreeCanvas tree={tree} />)
    expect(container.querySelectorAll('[data-tree-action-rail]')).toHaveLength(0)
  })

  it('root prompt card uses "Clone tree" label; non-root cards use "Branch from here"', () => {
    const tree = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r')])
    const callbacks: ActionCallbacks = {
      onRefresh: jest.fn(),
      onBranch: jest.fn(),
    }
    const { container } = render(<TreeCanvas tree={tree} actionCallbacks={callbacks} />)
    // react-flow nodes render with `visibility: hidden` in jsdom (no layout
    // engine). testing-library's `getByRole` filters by visibility, so we
    // query the DOM directly via the data-tree-node-id wrappers + their
    // descendant aria-labels — the same pattern we used for the Refresh
    // click test above.
    const rootCard = container.querySelector('[data-tree-node-id="r"][data-selected]')
    const userTurnCard = container.querySelector('[data-tree-node-id="u"][data-selected]')
    expect(rootCard).not.toBeNull()
    expect(userTurnCard).not.toBeNull()

    const rootBranchBtn = Array.from(rootCard!.querySelectorAll('button')).find((b) =>
      b.getAttribute('aria-label')?.match(/clone tree/i),
    )
    const utBranchBtn = Array.from(userTurnCard!.querySelectorAll('button')).find((b) =>
      b.getAttribute('aria-label')?.match(/branch from here/i),
    )
    expect(rootBranchBtn).toBeDefined()
    expect(utBranchBtn).toBeDefined()
  })
})

// ============================================================================
// 3. Rail position / accessibility surface
// ============================================================================

describe('ActionRail — accessibility', () => {
  it('each button carries an accessible name (aria-label) for screen readers', () => {
    const callbacks: ActionCallbacks = {
      onRefresh: jest.fn(),
      onBranch: jest.fn(),
      onDelete: jest.fn(),
      onOpenLinear: jest.fn(),
    }
    render(<ActionRail nodeId={nodeId('r')} callbacks={callbacks} branchLabel="Clone tree" />)
    const buttons = screen.getAllByRole('button')
    for (const b of buttons) {
      const name = b.getAttribute('aria-label')
      expect(name).toBeTruthy()
      expect(name!.length).toBeGreaterThan(0)
    }
  })

  it('does not expose future-only Branch-subtree copy in normal V1.0', () => {
    const callbacks: ActionCallbacks = { onRefresh: jest.fn(), onBranch: jest.fn() }
    render(<ActionRail nodeId={nodeId('r')} callbacks={callbacks} branchLabel="x" />)
    expect(screen.queryByRole('button', { name: /branch as subtree/i })).toBeNull()
    expect(screen.queryByTitle(/coming|future|available/i)).toBeNull()
  })

  it('rail carries data-tree-action-rail and data-tree-node-id for DOM scoping', () => {
    const callbacks: ActionCallbacks = { onRefresh: jest.fn(), onBranch: jest.fn() }
    const { container } = render(
      <ActionRail nodeId={nodeId('node-42')} callbacks={callbacks} branchLabel="x" />,
    )
    const rail = container.querySelector('[data-tree-action-rail]')
    expect(rail).not.toBeNull()
    expect(rail?.getAttribute('data-tree-node-id')).toBe(nodeId('node-42'))
  })
})

// ============================================================================
// 4. Wrapping inside the card preserves selection / data-tree-node-id
// ============================================================================

describe('CardFrame integration — rail does not break the selection contract', () => {
  it('TreeCanvas with callbacks preserves the data-tree-node-id wrapper attribute', () => {
    // Defense-in-depth: the PR5a/PR5b TreeCanvas test selector depends
    // on data-tree-node-id remaining on the outermost wrapper. The rail
    // sits INSIDE the card, not around it; the wrapper attribute stays
    // unchanged.
    const tree = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r')])
    const callbacks: ActionCallbacks = {
      onRefresh: jest.fn(),
      onBranch: jest.fn(),
    }
    const { container } = render(<TreeCanvas tree={tree} actionCallbacks={callbacks} />)
    const wrappers = container.querySelectorAll('[data-tree-node-id]')
    // 2 cards + 2 rails (each rail also tags itself for DOM scoping).
    // Filter to the card wrappers via the presence of data-selected.
    const cards = Array.from(wrappers).filter((el) =>
      el.hasAttribute('data-selected'),
    )
    expect(cards).toHaveLength(2)
  })
})

// ============================================================================
// 5. Hover-gate visibility (spec §2.2) — PR5h.4 review
// ============================================================================
//
// Spec contract: rail starts hidden and lifts to opacity 1 when the
// card is hovered OR contains a focused descendant OR carries
// data-selected="true". jsdom does NOT fire :hover or :focus-within
// pseudo-classes from synthetic events (visual-only effects require a
// real browser), but attribute selectors DO match — the data-selected
// branch is testable here; :hover and :focus-within are covered by the
// CSS source + Playwright later.

describe('ActionRail — visibility hover-gate (spec §2.2)', () => {
  it('rail opacity is 0 by default (card not selected, not hovered)', () => {
    // Mount via TreeCanvas so the CardFrame's hover-gate CSS applies.
    const tree = mkTree('r', [mkRoot('r')])
    const callbacks: ActionCallbacks = { onRefresh: jest.fn(), onBranch: jest.fn() }
    const { container } = render(<TreeCanvas tree={tree} actionCallbacks={callbacks} />)
    const rail = container.querySelector('[data-tree-action-rail]') as HTMLElement
    expect(rail).not.toBeNull()
    expect(window.getComputedStyle(rail).opacity).toBe('0')
  })

  it('rail opacity is 1 when the card frame has data-selected="true"', () => {
    // The cards forward react-flow's `selected` to CardFrame which writes
    // data-selected. We exercise that path by toggling the attribute on
    // the wrapper directly — the attribute selector in nodeCards.styles
    // picks up the visibility flip without needing to drive react-flow's
    // selection internals.
    const tree = mkTree('r', [mkRoot('r')])
    const callbacks: ActionCallbacks = { onRefresh: jest.fn(), onBranch: jest.fn() }
    const { container } = render(<TreeCanvas tree={tree} actionCallbacks={callbacks} />)
    const card = container.querySelector(
      '[data-tree-node-id="r"][data-selected]',
    ) as HTMLElement
    expect(card).not.toBeNull()
    card.setAttribute('data-selected', 'true')
    const rail = card.querySelector('[data-tree-action-rail]') as HTMLElement
    expect(window.getComputedStyle(rail).opacity).toBe('1')
  })
})

// ============================================================================
// 6. Refresh cost-preview tooltip (spec §2.2 Finding D.3) — PR6b
// ============================================================================
//
// `getRefreshCost(nodeId)` returns `{ calls, leaves }` so the operator
// sees the cost on hover BEFORE clicking the button that pops the
// cost-guardrail modal. Cures the "dismiss modal once, learn to ignore"
// failure mode the PR5h.4 reviewer named.

describe('ActionRail — Refresh cost-preview tooltip', () => {
  it('Refresh button tooltip is plain "Refresh" when getRefreshCost is NOT wired', () => {
    const callbacks: ActionCallbacks = { onRefresh: jest.fn() }
    render(<ActionRail nodeId={nodeId('r')} callbacks={callbacks} branchLabel="x" />)
    const btn = screen.getByRole('button', { name: /^refresh$/i })
    // aria-label is the screen-reader-visible name; matches the tooltip
    // content in the no-cost-preview case.
    expect(btn.getAttribute('aria-label')).toBe('Refresh')
  })

  it('Refresh button aria-label includes call+leaf preview when getRefreshCost returns a non-zero estimate', () => {
    const callbacks: ActionCallbacks = {
      onRefresh: jest.fn(),
      getRefreshCost: () => ({ calls: 60, leaves: 5 }),
    }
    render(<ActionRail nodeId={nodeId('r')} callbacks={callbacks} branchLabel="x" />)
    const btn = screen.getByRole('button', { name: /refresh/i })
    const aria = btn.getAttribute('aria-label') ?? ''
    expect(aria).toMatch(/refresh/i)
    expect(aria).toMatch(/60/)
    expect(aria).toMatch(/5/)
    expect(aria).toMatch(/leaf|leaves|call/i)
  })

  it('aria-label uses "1 leaf" (singular) for a single-leaf estimate', () => {
    const callbacks: ActionCallbacks = {
      onRefresh: jest.fn(),
      getRefreshCost: () => ({ calls: 2, leaves: 1 }),
    }
    render(<ActionRail nodeId={nodeId('r')} callbacks={callbacks} branchLabel="x" />)
    const btn = screen.getByRole('button', { name: /refresh/i })
    const aria = btn.getAttribute('aria-label') ?? ''
    expect(aria).toMatch(/1 leaf\b/i)
    expect(aria).not.toMatch(/1 leaves/i)
  })

  it('aria-label uses "1 call" (singular) for a single-call estimate', () => {
    const callbacks: ActionCallbacks = {
      onRefresh: jest.fn(),
      getRefreshCost: () => ({ calls: 1, leaves: 1 }),
    }
    render(<ActionRail nodeId={nodeId('r')} callbacks={callbacks} branchLabel="x" />)
    const btn = screen.getByRole('button', { name: /refresh/i })
    const aria = btn.getAttribute('aria-label') ?? ''
    expect(aria).toMatch(/1 call\b/i)
    expect(aria).not.toMatch(/1 calls/i)
  })

  it('aria-label degrades to plain "Refresh" when estimate is zero (nothing to dispatch)', () => {
    const callbacks: ActionCallbacks = {
      onRefresh: jest.fn(),
      getRefreshCost: () => ({ calls: 0, leaves: 0 }),
    }
    render(<ActionRail nodeId={nodeId('r')} callbacks={callbacks} branchLabel="x" />)
    const btn = screen.getByRole('button', { name: /refresh/i })
    expect(btn.getAttribute('aria-label')).toBe('Refresh (nothing to dispatch)')
    // Button remains clickable; the modal-gate (PR6a) and runner handle
    // the no-op gracefully. Hiding the button on cost=0 would lose the
    // operator's "I want to verify nothing's stale" intent.
    expect(btn.hasAttribute('disabled')).toBe(false)
  })

  it('getRefreshCost is invoked with this node id (not a sibling)', () => {
    const getRefreshCost = jest.fn(() => ({ calls: 5, leaves: 1 }))
    const callbacks: ActionCallbacks = {
      onRefresh: jest.fn(),
      getRefreshCost,
    }
    render(<ActionRail nodeId={nodeId('my-node')} callbacks={callbacks} branchLabel="x" />)
    expect(getRefreshCost).toHaveBeenCalledWith(nodeId('my-node'))
  })

  it('estimate changes when the host returns a different value on re-render', () => {
    const callbacks1: ActionCallbacks = {
      onRefresh: jest.fn(),
      getRefreshCost: () => ({ calls: 10, leaves: 2 }),
    }
    const callbacks2: ActionCallbacks = {
      onRefresh: callbacks1.onRefresh,
      getRefreshCost: () => ({ calls: 20, leaves: 4 }),
    }
    const { rerender } = render(
      <ActionRail nodeId={nodeId('r')} callbacks={callbacks1} branchLabel="x" />,
    )
    expect(
      screen.getByRole('button', { name: /refresh/i }).getAttribute('aria-label'),
    ).toMatch(/10.*2 leaves/)
    rerender(<ActionRail nodeId={nodeId('r')} callbacks={callbacks2} branchLabel="x" />)
    expect(
      screen.getByRole('button', { name: /refresh/i }).getAttribute('aria-label'),
    ).toMatch(/20.*4 leaves/)
  })
})
