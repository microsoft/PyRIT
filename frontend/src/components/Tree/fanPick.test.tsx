// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Tests for Pick / Unpick on fan children.
 *
 * Pinned contracts (per the rubber-duck-adjusted PR5f design):
 *   - Adapter widens every fan-child's `data` with `fanChildInfo`
 *     carrying `parentFanId`, `slotIndex`, `promoted`, `dimmed` so
 *     cards know their fan context without a tree lookup at render
 *     time. Non-fan children carry no `fanChildInfo`.
 *   - CardFrame applies a dim CSS class when `data.fanChildInfo?.dimmed`
 *     is true and a promoted-highlight class when `.promoted` is true.
 *   - When `onPickFanChild` is wired, the per-child action rail shows a
 *     CheckmarkCircle toggle icon: outline = pickable, filled = currently
 *     picked. Click toggles for own slot, switches to own slot when a
 *     sibling is picked.
 *   - When the fan is collapsed (stack rendering, PR5e) AND
 *     `onPickFanChild` is wired, the FanCard renders a "Pick…" button
 *     that opens a Fluent Menu listing every member with its
 *     state/slot; click an item to pick (or unpick if already picked).
 *   - FanCard's `pick: slot N` MetaRow stays read-only display with a
 *     tooltip clarifying V1.0 is visual-only.
 *   - `computeStackAggregate` extended with `members` so the popover
 *     has per-child data without a separate tree walk.
 */

import { fireEvent, render } from '@testing-library/react'
import { ReactFlowProvider } from '@xyflow/react'

import type { ActionCallbacks } from './actionRail'
import { conversationTreeToReactFlow } from './conversationTreeToReactFlow'
import { computeStackAggregate } from './fanStack'
import { FanCard, SendCard } from './nodeCards'
import { ActionCallbacksContext } from './actionCallbacksContext'
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
import type { FanNode, FanVariant, SendNode } from '../../runner/treeTypes'

function attemptVariants(n: number): FanVariant[] {
  return Array.from({ length: n }, () => ({ axis: 'attempt' as const, payload: {} }))
}

// ----------------------------------------------------------------------------
// Direct-mount helpers (tests that don't need TreeCanvas)
// ----------------------------------------------------------------------------

function mockSendProps(
  node: SendNode,
  fanChildInfo?: { parentFanId: string; slotIndex: number; promoted: boolean; dimmed: boolean },
  selected = false,
) {
  const data: { node: SendNode; fanChildInfo?: typeof fanChildInfo } = { node, fanChildInfo }
  return {
    id: node.id as string,
    data,
    selected,
  } as unknown as Parameters<typeof SendCard>[0]
}

function mockFanProps(
  fanNode: FanNode,
  extra: {
    stackedSummary?: ReturnType<typeof computeStackAggregate>
    selected?: boolean
  } = {},
) {
  const data: {
    node: FanNode
    stackedSummary?: ReturnType<typeof computeStackAggregate>
  } = { node: fanNode, stackedSummary: extra.stackedSummary }
  return {
    id: fanNode.id as string,
    data,
    selected: extra.selected ?? false,
  } as unknown as Parameters<typeof FanCard>[0]
}

function renderCardWith({
  ui,
  callbacks = null,
  stack = null,
}: {
  ui: React.ReactNode
  callbacks?: ActionCallbacks | null
  stack?: StackCollapseValue | null
}) {
  return render(
    <ActionCallbacksContext.Provider value={callbacks}>
      <StackCollapseContext.Provider value={stack}>
        <ReactFlowProvider>{ui}</ReactFlowProvider>
      </StackCollapseContext.Provider>
    </ActionCallbacksContext.Provider>,
  )
}

// ============================================================================
// 1. Adapter — fanChildInfo on fan children
// ============================================================================

describe('adapter — fanChildInfo on fan children', () => {
  it('attaches fanChildInfo to every fan child with parentFanId + slotIndex', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f', 'u', { axis: 'attempt', variants: attemptVariants(3) }),
      mkSend('s_a', 'f'),
      mkSend('s_b', 'f'),
      mkSend('s_c', 'f'),
    ])
    const { nodes } = conversationTreeToReactFlow(tree)
    const byId = new Map(nodes.map((n) => [n.id, n]))
    for (const id of ['s_a', 's_b', 's_c']) {
      const n = byId.get(nodeId(id))!
      if (n.type === 'send') {
        expect(n.data.fanChildInfo).toBeDefined()
        expect(n.data.fanChildInfo?.parentFanId).toBe(nodeId('f'))
        expect(typeof n.data.fanChildInfo?.slotIndex).toBe('number')
      }
    }
  })

  it('does NOT attach fanChildInfo to non-fan children', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkSend('s', 'u'),
    ])
    const { nodes } = conversationTreeToReactFlow(tree)
    const sendNode = nodes.find((n) => n.id === nodeId('s'))!
    if (sendNode.type === 'send') {
      expect(sendNode.data.fanChildInfo).toBeUndefined()
    }
    const userTurnNode = nodes.find((n) => n.id === nodeId('u'))!
    if (userTurnNode.type === 'user_turn') {
      expect(userTurnNode.data.fanChildInfo).toBeUndefined()
    }
  })

  it('marks the promoted child with promoted=true and the rest with dimmed=true', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f', 'u', {
        axis: 'attempt',
        variants: attemptVariants(3),
        promotedChildSlotIndex: 1,
      }),
      mkSend('s_a', 'f'),
      mkSend('s_b', 'f'),
      mkSend('s_c', 'f'),
    ])
    const { nodes } = conversationTreeToReactFlow(tree)
    const byId = new Map(nodes.map((n) => [n.id, n]))
    // mkTree's auto-numbering assigns slotIndex 0,1,2 by ordinal: s_a=0, s_b=1, s_c=2
    const aInfo = byId.get(nodeId('s_a'))!.data.fanChildInfo
    const bInfo = byId.get(nodeId('s_b'))!.data.fanChildInfo
    const cInfo = byId.get(nodeId('s_c'))!.data.fanChildInfo
    expect(bInfo?.promoted).toBe(true)
    expect(bInfo?.dimmed).toBe(false)
    expect(aInfo?.promoted).toBe(false)
    expect(aInfo?.dimmed).toBe(true)
    expect(cInfo?.promoted).toBe(false)
    expect(cInfo?.dimmed).toBe(true)
  })

  it('all children have promoted=false and dimmed=false when no slot is promoted', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f', 'u', { axis: 'attempt', variants: attemptVariants(2) }),
      mkSend('s_a', 'f'),
      mkSend('s_b', 'f'),
    ])
    const { nodes } = conversationTreeToReactFlow(tree)
    const byId = new Map(nodes.map((n) => [n.id, n]))
    for (const id of ['s_a', 's_b']) {
      const info = byId.get(nodeId(id))!.data.fanChildInfo
      expect(info?.promoted).toBe(false)
      expect(info?.dimmed).toBe(false)
    }
  })
})

// ============================================================================
// 2. computeStackAggregate — members list for the popover
// ============================================================================

describe('computeStackAggregate — members list', () => {
  it('returns a members array with id + slotIndex + state for each child (in slot order)', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f', 'u', { axis: 'attempt', variants: attemptVariants(3) }),
      mkSend('s_a', 'f', undefined, { state: 'clean' }),
      mkSend('s_b', 'f', undefined, { state: 'failed' }),
      mkSend('s_c', 'f', undefined, { state: 'stale' }),
    ])
    const agg = computeStackAggregate(tree, nodeId('f'))
    expect(agg.members).toEqual([
      { id: nodeId('s_a'), slotIndex: 0, state: 'clean' },
      { id: nodeId('s_b'), slotIndex: 1, state: 'failed' },
      { id: nodeId('s_c'), slotIndex: 2, state: 'stale' },
    ])
  })

  it('returns an empty members array for a non-fan node', () => {
    const tree = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r')])
    expect(computeStackAggregate(tree, nodeId('u')).members).toEqual([])
  })
})

// ============================================================================
// 3. CardFrame — dim + promoted styling via data attributes
// ============================================================================

describe('CardFrame — fan-child dim / promoted', () => {
  it('applies data-dimmed="true" to a dimmed fan child', () => {
    const node = mkSend('s', 'f')
    const { container } = renderCardWith({
      ui: (
        <SendCard
          {...mockSendProps(node, { parentFanId: 'f', slotIndex: 0, promoted: false, dimmed: true })}
        />
      ),
    })
    const card = container.querySelector('[data-tree-node-id]')
    expect(card?.getAttribute('data-dimmed')).toBe('true')
    expect(card?.getAttribute('data-promoted')).toBe('false')
  })

  it('applies data-promoted="true" to the promoted fan child', () => {
    const node = mkSend('s', 'f')
    const { container } = renderCardWith({
      ui: (
        <SendCard
          {...mockSendProps(node, { parentFanId: 'f', slotIndex: 0, promoted: true, dimmed: false })}
        />
      ),
    })
    const card = container.querySelector('[data-tree-node-id]')
    expect(card?.getAttribute('data-promoted')).toBe('true')
    expect(card?.getAttribute('data-dimmed')).toBe('false')
  })

  it('emits data-dimmed="false" and data-promoted="false" for a non-fan child', () => {
    const node = mkSend('s', 'u') // not a fan child
    const { container } = renderCardWith({
      ui: <SendCard {...mockSendProps(node)} />,
    })
    const card = container.querySelector('[data-tree-node-id]')
    expect(card?.getAttribute('data-dimmed')).toBe('false')
    expect(card?.getAttribute('data-promoted')).toBe('false')
  })
})

// ============================================================================
// 4. Per-child Pick toggle on the action rail
// ============================================================================

describe('ActionRail — Pick toggle (fan child)', () => {
  it('renders a Pick toggle button when onPickFanChild is supplied AND data.fanChildInfo is present', () => {
    const node = mkSend('s', 'f')
    const onPickFanChild = jest.fn()
    const { container } = renderCardWith({
      ui: (
        <SendCard
          {...mockSendProps(node, {
            parentFanId: 'f',
            slotIndex: 0,
            promoted: false,
            dimmed: false,
          })}
        />
      ),
      callbacks: { onPickFanChild },
    })
    const card = container.querySelector('[data-tree-node-id="s"]')!
    const pickBtn = Array.from(card.querySelectorAll('button')).find((b) =>
      b.getAttribute('aria-label')?.match(/pick/i),
    )
    expect(pickBtn).toBeDefined()
  })

  it('does NOT render a Pick toggle on a non-fan child', () => {
    const node = mkSend('s', 'u') // not under a fan
    const onPickFanChild = jest.fn()
    const { container } = renderCardWith({
      ui: <SendCard {...mockSendProps(node)} />,
      callbacks: { onPickFanChild },
    })
    const card = container.querySelector('[data-tree-node-id="s"]')!
    const pickBtn = Array.from(card.querySelectorAll('button')).find((b) =>
      b.getAttribute('aria-label')?.match(/pick/i),
    )
    expect(pickBtn).toBeUndefined()
  })

  it('does NOT render a Pick toggle when onPickFanChild is undefined', () => {
    const node = mkSend('s', 'f')
    const { container } = renderCardWith({
      ui: (
        <SendCard
          {...mockSendProps(node, { parentFanId: 'f', slotIndex: 0, promoted: false, dimmed: false })}
        />
      ),
      callbacks: { onRefresh: jest.fn() },
    })
    const card = container.querySelector('[data-tree-node-id="s"]')!
    const pickBtn = Array.from(card.querySelectorAll('button')).find((b) =>
      b.getAttribute('aria-label')?.match(/pick/i),
    )
    expect(pickBtn).toBeUndefined()
  })

  it('aria-label says "Pick this attempt" when not promoted', () => {
    const node = mkSend('s', 'f')
    const { container } = renderCardWith({
      ui: (
        <SendCard
          {...mockSendProps(node, { parentFanId: 'f', slotIndex: 0, promoted: false, dimmed: false })}
        />
      ),
      callbacks: { onPickFanChild: jest.fn() },
    })
    const card = container.querySelector('[data-tree-node-id="s"]')!
    const pickBtn = Array.from(card.querySelectorAll('button')).find((b) =>
      b.getAttribute('aria-label')?.match(/pick/i),
    )!
    expect(pickBtn.getAttribute('aria-label')).toMatch(/pick this attempt/i)
  })

  it('aria-label says "Unpick" when this child is promoted', () => {
    const node = mkSend('s', 'f')
    const { container } = renderCardWith({
      ui: (
        <SendCard
          {...mockSendProps(node, { parentFanId: 'f', slotIndex: 0, promoted: true, dimmed: false })}
        />
      ),
      callbacks: { onPickFanChild: jest.fn() },
    })
    const card = container.querySelector('[data-tree-node-id="s"]')!
    const pickBtn = Array.from(card.querySelectorAll('button')).find((b) =>
      b.getAttribute('aria-label')?.match(/unpick/i),
    )!
    expect(pickBtn.getAttribute('aria-label')).toMatch(/unpick/i)
  })

  it('click on a pickable child invokes onPickFanChild(parentFanId, slotIndex)', () => {
    const node = mkSend('s_b', 'f')
    const onPickFanChild = jest.fn()
    const { container } = renderCardWith({
      ui: (
        <SendCard
          {...mockSendProps(node, { parentFanId: 'f', slotIndex: 1, promoted: false, dimmed: false })}
        />
      ),
      callbacks: { onPickFanChild },
    })
    const card = container.querySelector('[data-tree-node-id="s_b"]')!
    const pickBtn = Array.from(card.querySelectorAll('button')).find((b) =>
      b.getAttribute('aria-label')?.match(/pick/i),
    )!
    fireEvent.click(pickBtn)
    expect(onPickFanChild).toHaveBeenCalledTimes(1)
    expect(onPickFanChild).toHaveBeenCalledWith(nodeId('f'), 1)
  })

  it('click on a promoted child invokes onPickFanChild(parentFanId, null) (unpick)', () => {
    const node = mkSend('s_b', 'f')
    const onPickFanChild = jest.fn()
    const { container } = renderCardWith({
      ui: (
        <SendCard
          {...mockSendProps(node, { parentFanId: 'f', slotIndex: 1, promoted: true, dimmed: false })}
        />
      ),
      callbacks: { onPickFanChild },
    })
    const card = container.querySelector('[data-tree-node-id="s_b"]')!
    const pickBtn = Array.from(card.querySelectorAll('button')).find((b) =>
      b.getAttribute('aria-label')?.match(/unpick/i),
    )!
    fireEvent.click(pickBtn)
    expect(onPickFanChild).toHaveBeenCalledTimes(1)
    expect(onPickFanChild).toHaveBeenCalledWith(nodeId('f'), null)
  })
})

// ============================================================================
// 5. FanCard MetaRow — read-only tooltip
// ============================================================================

describe('FanCard — pick MetaRow tooltip', () => {
  it('renders the "pick: slot N" MetaRow with a title attr clarifying V1.0 is visual-only', () => {
    const fan = mkFan('f', 'parent', {
      axis: 'attempt',
      variants: attemptVariants(3),
      promotedChildSlotIndex: 1,
    })
    const { container } = renderCardWith({
      ui: <FanCard {...mockFanProps(fan)} />,
    })
    // The MetaRow is rendered when promotedChildSlotIndex is non-null.
    const card = container.querySelector('[data-tree-node-id="f"]')!
    const pickRow = Array.from(card.querySelectorAll('[title]')).find((el) =>
      el.textContent?.match(/pick/i),
    )
    expect(pickRow).toBeDefined()
    expect(pickRow?.getAttribute('title')).toMatch(/visual focus|future|Stack-edit|refresh/i)
  })

  it('does NOT render the MetaRow when no slot is promoted (PR5b behavior preserved)', () => {
    const fan = mkFan('f', 'parent', { axis: 'attempt', variants: attemptVariants(3) })
    const { container } = renderCardWith({
      ui: <FanCard {...mockFanProps(fan)} />,
    })
    const card = container.querySelector('[data-tree-node-id="f"]')!
    expect(card.textContent?.toLowerCase()).not.toMatch(/pick:/i)
  })
})

// ============================================================================
// 6. Collapsed-stack Pick popover (FanCard)
// ============================================================================

describe('FanCard — collapsed-stack Pick popover', () => {
  it('renders a "Pick…" button on the stack summary when onPickFanChild is wired', () => {
    const fan = mkFan('f', 'parent', { axis: 'attempt', variants: attemptVariants(5) })
    const { container } = renderCardWith({
      ui: (
        <FanCard
          {...mockFanProps(fan, {
            stackedSummary: {
              childKind: 'send',
              total: 5,
              byState: {
                clean: 5,
                edited: 0,
                stale: 0,
                running: 0,
                failed: 0,
                cancelled: 0,
                draft: 0,
              },
              members: [0, 1, 2, 3, 4].map((i) => ({
                id: nodeId(`s_${i}`),
                slotIndex: i,
                state: 'clean' as const,
              })),
            },
          })}
        />
      ),
      callbacks: { onPickFanChild: jest.fn() },
    })
    const card = container.querySelector('[data-tree-node-id="f"]')!
    const pickBtn = Array.from(card.querySelectorAll('button')).find((b) =>
      b.getAttribute('aria-label')?.match(/pick.*from stack/i),
    )
    expect(pickBtn).toBeDefined()
  })

  it('does NOT render the "Pick…" button when onPickFanChild is undefined', () => {
    const fan = mkFan('f', 'parent', { axis: 'attempt', variants: attemptVariants(5) })
    const { container } = renderCardWith({
      ui: (
        <FanCard
          {...mockFanProps(fan, {
            stackedSummary: {
              childKind: 'send',
              total: 5,
              byState: {
                clean: 5,
                edited: 0,
                stale: 0,
                running: 0,
                failed: 0,
                cancelled: 0,
                draft: 0,
              },
              members: [],
            },
          })}
        />
      ),
    })
    const card = container.querySelector('[data-tree-node-id="f"]')!
    const pickBtn = Array.from(card.querySelectorAll('button')).find((b) =>
      b.getAttribute('aria-label')?.match(/pick.*from stack/i),
    )
    expect(pickBtn).toBeUndefined()
  })

  it('clicking "Pick…" opens a menu listing each member with their slot index', () => {
    const fan = mkFan('f', 'parent', { axis: 'attempt', variants: attemptVariants(3) })
    const { container } = renderCardWith({
      ui: (
        <FanCard
          {...mockFanProps(fan, {
            stackedSummary: {
              childKind: 'send',
              total: 3,
              byState: {
                clean: 2,
                edited: 0,
                stale: 0,
                running: 0,
                failed: 1,
                cancelled: 0,
                draft: 0,
              },
              members: [
                { id: nodeId('s_0'), slotIndex: 0, state: 'clean' },
                { id: nodeId('s_1'), slotIndex: 1, state: 'clean' },
                { id: nodeId('s_2'), slotIndex: 2, state: 'failed' },
              ],
            },
          })}
        />
      ),
      callbacks: { onPickFanChild: jest.fn() },
    })
    const card = container.querySelector('[data-tree-node-id="f"]')!
    const pickBtn = Array.from(card.querySelectorAll('button')).find((b) =>
      b.getAttribute('aria-label')?.match(/pick.*from stack/i),
    )!
    fireEvent.click(pickBtn)
    const items = Array.from(document.querySelectorAll('[role="menuitem"]'))
    expect(items.length).toBe(3)
    const labels = items.map((i) => i.textContent ?? '').join('|')
    // Members rendered with slot indices.
    expect(labels).toMatch(/slot 0/i)
    expect(labels).toMatch(/slot 1/i)
    expect(labels).toMatch(/slot 2/i)
  })

  it('clicking a menu item invokes onPickFanChild(fanId, slotIndex)', () => {
    const fan = mkFan('f', 'parent', { axis: 'attempt', variants: attemptVariants(3) })
    const onPickFanChild = jest.fn()
    const { container } = renderCardWith({
      ui: (
        <FanCard
          {...mockFanProps(fan, {
            stackedSummary: {
              childKind: 'send',
              total: 3,
              byState: {
                clean: 3,
                edited: 0,
                stale: 0,
                running: 0,
                failed: 0,
                cancelled: 0,
                draft: 0,
              },
              members: [0, 1, 2].map((i) => ({
                id: nodeId(`s_${i}`),
                slotIndex: i,
                state: 'clean' as const,
              })),
            },
          })}
        />
      ),
      callbacks: { onPickFanChild },
    })
    const card = container.querySelector('[data-tree-node-id="f"]')!
    fireEvent.click(
      Array.from(card.querySelectorAll('button')).find((b) =>
        b.getAttribute('aria-label')?.match(/pick.*from stack/i),
      )!,
    )
    const items = Array.from(document.querySelectorAll('[role="menuitem"]'))
    const targetItem = items.find((i) => i.textContent?.match(/slot 1/i))!
    fireEvent.click(targetItem)
    expect(onPickFanChild).toHaveBeenCalledWith(nodeId('f'), 1)
  })

  it('the currently-promoted member shows an indicator AND clicking it unpicks (null)', () => {
    const fan = mkFan('f', 'parent', {
      axis: 'attempt',
      variants: attemptVariants(3),
      promotedChildSlotIndex: 1,
    })
    const onPickFanChild = jest.fn()
    const { container } = renderCardWith({
      ui: (
        <FanCard
          {...mockFanProps(fan, {
            stackedSummary: {
              childKind: 'send',
              total: 3,
              byState: {
                clean: 3,
                edited: 0,
                stale: 0,
                running: 0,
                failed: 0,
                cancelled: 0,
                draft: 0,
              },
              members: [0, 1, 2].map((i) => ({
                id: nodeId(`s_${i}`),
                slotIndex: i,
                state: 'clean' as const,
              })),
            },
          })}
        />
      ),
      callbacks: { onPickFanChild },
    })
    const card = container.querySelector('[data-tree-node-id="f"]')!
    fireEvent.click(
      Array.from(card.querySelectorAll('button')).find((b) =>
        b.getAttribute('aria-label')?.match(/pick.*from stack/i),
      )!,
    )
    const items = Array.from(document.querySelectorAll('[role="menuitem"]'))
    // The slot-1 item should be marked as currently picked (✓ glyph or text).
    const promotedItem = items.find((i) => i.textContent?.match(/slot 1/i))!
    expect(promotedItem.textContent).toMatch(/\(picked\)|✓/i)
    // Clicking it unpicks.
    fireEvent.click(promotedItem)
    expect(onPickFanChild).toHaveBeenCalledWith(nodeId('f'), null)
  })
})

// ============================================================================
// 7. TreeCanvas — end-to-end Pick round-trip (expanded fan)
// ============================================================================

describe('TreeCanvas — Pick round-trip via per-child icon', () => {
  it('clicking a fan-child Pick icon invokes onPickFanChild(fanId, slotIndex)', () => {
    // 3-leaf fan: NOT auto-collapsed (N=3 boundary), so per-child cards render.
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f', 'u', { axis: 'attempt', variants: attemptVariants(3) }),
      mkSend('s_a', 'f'),
      mkSend('s_b', 'f'),
      mkSend('s_c', 'f'),
    ])
    const onPickFanChild = jest.fn()
    const { container } = render(
      <TreeCanvas tree={tree} actionCallbacks={{ onPickFanChild }} />,
    )
    const sbCard = container.querySelector('[data-tree-node-id="s_b"]')!
    const pickBtn = Array.from(sbCard.querySelectorAll('button')).find((b) =>
      b.getAttribute('aria-label')?.match(/pick/i),
    )!
    fireEvent.click(pickBtn)
    expect(onPickFanChild).toHaveBeenCalledWith(nodeId('f'), 1)
  })

  it('promoted child renders with data-promoted=true; siblings render with data-dimmed=true', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f', 'u', {
        axis: 'attempt',
        variants: attemptVariants(3),
        promotedChildSlotIndex: 1,
      }),
      mkSend('s_a', 'f'),
      mkSend('s_b', 'f'),
      mkSend('s_c', 'f'),
    ])
    const { container } = render(<TreeCanvas tree={tree} />)
    expect(container.querySelector('[data-tree-node-id="s_b"]')?.getAttribute('data-promoted')).toBe(
      'true',
    )
    expect(container.querySelector('[data-tree-node-id="s_a"]')?.getAttribute('data-dimmed')).toBe(
      'true',
    )
    expect(container.querySelector('[data-tree-node-id="s_c"]')?.getAttribute('data-dimmed')).toBe(
      'true',
    )
  })
})
