// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Tests for the per-edge `+` insert chip + popover.
 *
 * Test approach: mount the `InsertEdge` component DIRECTLY (wrapped in
 * the ReactFlow store providers) rather than going through TreeCanvas.
 * Reason: react-flow's edge layer is gated on full node measurement
 * (handleBounds populated via ResizeObserver), which jsdom can't
 * simulate cleanly without invasive setupTests changes. Direct mount
 * exercises the component's full surface (kind-aware menu, callback
 * invocation, chip suppression) without the layout dependency.
 *
 * Integration with TreeCanvas is covered by:
 *   - the adapter test (PR5a/d) that asserts edges carry `type: 'insert'`
 *   - the edgeTypes registry test below — minimal smoke test that the
 *     registry exports the InsertEdge component
 *
 * Pinned contracts:
 *   - chip renders when onEdgeInsert callback is supplied
 *   - chip is suppressed when callback is undefined (backwards-compat)
 *   - menu options vary by parent kind (root vs user_turn vs send, etc.)
 *   - selecting an option invokes onEdgeInsert with the right discriminant
 *   - V1.1 fan axes render disabled
 *   - score / fan parents render the edge WITHOUT a chip
 */

import { fireEvent, render } from '@testing-library/react'
import { Position, ReactFlowProvider } from '@xyflow/react'
import type { EdgeProps } from '@xyflow/react'

import { ActionCallbacksContext } from './actionCallbacksContext'
import type { ActionCallbacks, EdgeInsertKind } from './actionRail'
import { conversationTreeToReactFlow } from './conversationTreeToReactFlow'
import { InsertEdge } from './InsertEdge'
import { treeEdgeTypes } from './treeEdgeTypes'
import type { TreeFlowEdge } from './conversationTreeToReactFlow'
import {
  mkFan,
  mkRoot,
  mkScore,
  mkSend,
  mkTree,
  mkUserTurn,
  nodeId,
} from '../../runner/testHelpers'
import type { ConversationTreeNodeKind } from '../../runner/treeTypes'

// ----------------------------------------------------------------------------
// Direct-mount harness
// ----------------------------------------------------------------------------

function mkEdgeProps(
  parentKind: ConversationTreeNodeKind,
  source = 'parent',
  target = 'child',
): EdgeProps<TreeFlowEdge> {
  return {
    id: `${source}->${target}`,
    source,
    target,
    sourceX: 0,
    sourceY: 0,
    targetX: 100,
    targetY: 100,
    sourcePosition: Position.Bottom,
    targetPosition: Position.Top,
    data: { slotIndex: 0, parentKind },
    type: 'insert',
    style: undefined,
  } as unknown as EdgeProps<TreeFlowEdge>
}

function renderEdge(
  props: EdgeProps<TreeFlowEdge>,
  callbacks: ActionCallbacks | null,
) {
  // EdgeLabelRenderer portals into the document; tests query
  // `document.querySelector` for chip elements. SVG wrapping is required
  // because BaseEdge renders a <path> via react-flow's SVG layer.
  return render(
    <ActionCallbacksContext.Provider value={callbacks}>
      <ReactFlowProvider>
        <svg>
          <InsertEdge {...props} />
        </svg>
      </ReactFlowProvider>
    </ActionCallbacksContext.Provider>,
  )
}

// ============================================================================
// 1. Chip presence / suppression
// ============================================================================

describe('InsertEdge — chip presence', () => {
  it('renders a chip when onEdgeInsert is supplied', () => {
    const callbacks: ActionCallbacks = { onEdgeInsert: jest.fn() }
    renderEdge(mkEdgeProps('user_turn'), callbacks)
    expect(document.querySelector('[data-tree-edge-insert]')).not.toBeNull()
  })

  it('renders NO chip when onEdgeInsert is undefined (backwards-compat)', () => {
    const callbacks: ActionCallbacks = { onRefresh: jest.fn() }
    renderEdge(mkEdgeProps('user_turn'), callbacks)
    expect(document.querySelector('[data-tree-edge-insert]')).toBeNull()
  })

  it('renders NO chip when ActionCallbacksContext is null (TreeCanvas without callbacks prop)', () => {
    renderEdge(mkEdgeProps('user_turn'), null)
    expect(document.querySelector('[data-tree-edge-insert]')).toBeNull()
  })

  it('renders NO chip when parent is a Score (terminal)', () => {
    const callbacks: ActionCallbacks = { onEdgeInsert: jest.fn() }
    renderEdge(mkEdgeProps('score'), callbacks)
    expect(document.querySelector('[data-tree-edge-insert]')).toBeNull()
  })

  it('renders NO chip when parent is a Fan (variants managed via FanCard +)', () => {
    const callbacks: ActionCallbacks = { onEdgeInsert: jest.fn() }
    renderEdge(mkEdgeProps('fan'), callbacks)
    expect(document.querySelector('[data-tree-edge-insert]')).toBeNull()
  })

  it('chip carries data-source-id + data-target-id + data-source-kind for DOM scoping', () => {
    const callbacks: ActionCallbacks = { onEdgeInsert: jest.fn() }
    renderEdge(mkEdgeProps('root_prompt', 'rid', 'cid'), callbacks)
    const chip = document.querySelector('[data-tree-edge-insert]')
    expect(chip?.getAttribute('data-source-id')).toBe('rid')
    expect(chip?.getAttribute('data-target-id')).toBe('cid')
    expect(chip?.getAttribute('data-source-kind')).toBe('root_prompt')
  })
})

// ============================================================================
// 2. Kind-aware menu options
// ============================================================================

describe('InsertEdge — menu options per parent kind', () => {
  function openMenu(parentKind: ConversationTreeNodeKind): HTMLElement[] {
    const callbacks: ActionCallbacks = { onEdgeInsert: jest.fn() }
    renderEdge(mkEdgeProps(parentKind), callbacks)
    const chip = document.querySelector('[data-tree-edge-insert]')!
    const chipBtn = chip.querySelector('button')!
    fireEvent.click(chipBtn)
    return Array.from(document.querySelectorAll('[role="menuitem"]')) as HTMLElement[]
  }

  it('after a Send: Follow-up + Inject + Score + Fan attempt + Fan converter (V1.0 axes)', () => {
    const items = openMenu('send')
    const labels = items.map((i) => i.textContent ?? '').join('|').toLowerCase()
    expect(labels).toMatch(/follow-up/)
    expect(labels).toMatch(/inject/)
    expect(labels).toMatch(/score/)
    expect(labels).toMatch(/fan.*attempt/)
    expect(labels).toMatch(/fan.*converter/)
  })

  it('after a UserTurn: Send + Append converter + Fan converter (no Score, no attempt-fan)', () => {
    const items = openMenu('user_turn')
    const labels = items.map((i) => i.textContent ?? '').join('|').toLowerCase()
    expect(labels).toMatch(/send/)
    expect(labels).toMatch(/append converter/)
    // No Score / Inject assistant text under UserTurn — only legal after a Send.
    expect(labels).not.toMatch(/score/)
    expect(labels).not.toMatch(/inject/)
  })

  it('after a RootPrompt: Follow-up + Inject + Send', () => {
    const items = openMenu('root_prompt')
    const labels = items.map((i) => i.textContent ?? '').join('|').toLowerCase()
    expect(labels).toMatch(/follow-up/)
    expect(labels).toMatch(/send/)
    expect(labels).toMatch(/inject/)
  })

  it('after an ImportMessage: Follow-up + Inject + Send (same as RootPrompt)', () => {
    const items = openMenu('import_message')
    const labels = items.map((i) => i.textContent ?? '').join('|').toLowerCase()
    expect(labels).toMatch(/follow-up/)
    expect(labels).toMatch(/send/)
  })

  it('V1.1 axes (Fan prompt, Fan target) render disabled', () => {
    const items = openMenu('send')
    const v11Items = items.filter((item) =>
      (item.textContent ?? '').toLowerCase().match(/fan.*prompt|fan.*target/),
    )
    // Pin that the disabled stubs actually render — without this guard the
    // for-loop below passes vacuously if a regression removes the V1.1 items.
    expect(v11Items.length).toBeGreaterThanOrEqual(2)
    for (const item of v11Items) {
      expect(item.getAttribute('aria-disabled')).toBe('true')
    }
  })
})

// ============================================================================
// 3. Callback invocation
// ============================================================================

describe('InsertEdge — onEdgeInsert callback', () => {
  function clickFirstEnabledItemMatching(pattern: RegExp): void {
    const items = Array.from(document.querySelectorAll('[role="menuitem"]'))
    const target = items.find(
      (i) => i.textContent?.match(pattern) && i.getAttribute('aria-disabled') !== 'true',
    ) as HTMLElement | undefined
    expect(target).toBeDefined()
    fireEvent.click(target!)
  }

  it('selecting "Follow-up user message" invokes onEdgeInsert(parent, child, "follow_up_user_turn")', () => {
    const onEdgeInsert = jest.fn()
    renderEdge(mkEdgeProps('send', 's', 'u2'), { onEdgeInsert })
    fireEvent.click(document.querySelector('[data-tree-edge-insert] button')!)
    clickFirstEnabledItemMatching(/follow-up/i)
    expect(onEdgeInsert).toHaveBeenCalledTimes(1)
    const [parentId, childId, kind] = onEdgeInsert.mock.calls[0] as [
      string,
      string,
      EdgeInsertKind,
    ]
    expect(parentId).toBe(nodeId('s'))
    expect(childId).toBe(nodeId('u2'))
    expect(kind).toBe('follow_up_user_turn')
  })

  it('selecting "Send to target" invokes onEdgeInsert with kind="send"', () => {
    const onEdgeInsert = jest.fn()
    renderEdge(mkEdgeProps('user_turn', 'u', 's'), { onEdgeInsert })
    fireEvent.click(document.querySelector('[data-tree-edge-insert] button')!)
    clickFirstEnabledItemMatching(/send to target/i)
    expect(onEdgeInsert).toHaveBeenCalledWith(nodeId('u'), nodeId('s'), 'send')
  })

  it('selecting "Inject assistant text" invokes onEdgeInsert with kind="inject_assistant_text"', () => {
    const onEdgeInsert = jest.fn()
    renderEdge(mkEdgeProps('send', 's', 'next'), { onEdgeInsert })
    fireEvent.click(document.querySelector('[data-tree-edge-insert] button')!)
    clickFirstEnabledItemMatching(/inject/i)
    expect(onEdgeInsert).toHaveBeenCalledWith(nodeId('s'), nodeId('next'), 'inject_assistant_text')
  })

  it('selecting "Score" invokes onEdgeInsert with kind="score"', () => {
    const onEdgeInsert = jest.fn()
    renderEdge(mkEdgeProps('send', 's', 'sc'), { onEdgeInsert })
    fireEvent.click(document.querySelector('[data-tree-edge-insert] button')!)
    clickFirstEnabledItemMatching(/^score$/i)
    expect(onEdgeInsert).toHaveBeenCalledWith(nodeId('s'), nodeId('sc'), 'score')
  })

  it('selecting "Append converter" invokes onEdgeInsert with kind="append_converter"', () => {
    const onEdgeInsert = jest.fn()
    renderEdge(mkEdgeProps('user_turn', 'u', 's'), { onEdgeInsert })
    fireEvent.click(document.querySelector('[data-tree-edge-insert] button')!)
    clickFirstEnabledItemMatching(/append converter/i)
    expect(onEdgeInsert).toHaveBeenCalledWith(nodeId('u'), nodeId('s'), 'append_converter')
  })

  it('selecting "Fan out: attempt" invokes onEdgeInsert with kind="fan_attempt"', () => {
    const onEdgeInsert = jest.fn()
    renderEdge(mkEdgeProps('send', 's', 'u2'), { onEdgeInsert })
    fireEvent.click(document.querySelector('[data-tree-edge-insert] button')!)
    clickFirstEnabledItemMatching(/fan out: attempt$/i)
    expect(onEdgeInsert).toHaveBeenCalledWith(nodeId('s'), nodeId('u2'), 'fan_attempt')
  })

  it('selecting "Fan out: converter" invokes onEdgeInsert with kind="fan_converter"', () => {
    const onEdgeInsert = jest.fn()
    renderEdge(mkEdgeProps('send', 's', 'u2'), { onEdgeInsert })
    fireEvent.click(document.querySelector('[data-tree-edge-insert] button')!)
    clickFirstEnabledItemMatching(/fan out: converter$/i)
    expect(onEdgeInsert).toHaveBeenCalledWith(nodeId('s'), nodeId('u2'), 'fan_converter')
  })

  it('disabled V1.1 fan-axis items do NOT invoke onEdgeInsert when clicked', () => {
    const onEdgeInsert = jest.fn()
    renderEdge(mkEdgeProps('send'), { onEdgeInsert })
    fireEvent.click(document.querySelector('[data-tree-edge-insert] button')!)
    const items = Array.from(document.querySelectorAll('[role="menuitem"]'))
    const disabledFan = items.find((i) =>
      i.textContent?.match(/fan.*prompt|fan.*target/i),
    ) as HTMLElement | undefined
    // Pin that the disabled item exists before clicking it — a removed stub
    // would make the assertion below vacuous-pass without this guard.
    expect(disabledFan).toBeDefined()
    fireEvent.click(disabledFan!)
    expect(onEdgeInsert).not.toHaveBeenCalled()
  })
})

// ============================================================================
// 4. Accessibility
// ============================================================================

describe('InsertEdge — accessibility', () => {
  it('chip button has aria-label "Insert after <parent kind>"', () => {
    const callbacks: ActionCallbacks = { onEdgeInsert: jest.fn() }
    renderEdge(mkEdgeProps('user_turn'), callbacks)
    const btn = document.querySelector('[data-tree-edge-insert] button')!
    const aria = btn.getAttribute('aria-label')
    expect(aria).toMatch(/insert after/i)
  })
})

// ============================================================================
// 5. Adapter — parentKind on edge data
// ============================================================================

describe('conversationTreeToReactFlow — edge.data.parentKind', () => {
  it('each edge carries the source node kind on data.parentKind', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkSend('s', 'u'),
      mkScore('sc', 's'),
    ])
    const { edges } = conversationTreeToReactFlow(tree)
    const byPair = new Map(edges.map((e) => [`${e.source}->${e.target}`, e]))
    expect(byPair.get('r->u')?.data?.parentKind).toBe('root_prompt')
    expect(byPair.get('u->s')?.data?.parentKind).toBe('user_turn')
    expect(byPair.get('s->sc')?.data?.parentKind).toBe('send')
  })

  it('fan-child edges carry parentKind="fan" (so InsertEdge suppresses the chip)', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f', 'u', {
        axis: 'attempt',
        variants: [
          { axis: 'attempt', payload: {} },
          { axis: 'attempt', payload: {} },
        ],
      }),
      mkSend('s_a', 'f'),
      mkSend('s_b', 'f'),
    ])
    const { edges } = conversationTreeToReactFlow(tree)
    const fanEdges = edges.filter((e) => e.source === 'f')
    expect(fanEdges).toHaveLength(2)
    for (const e of fanEdges) {
      expect(e.data?.parentKind).toBe('fan')
    }
  })
})

// ============================================================================
// 6. Registry smoke test
// ============================================================================

describe('treeEdgeTypes registry', () => {
  it('registers InsertEdge under the "insert" key', () => {
    expect(treeEdgeTypes.insert).toBe(InsertEdge)
  })
})
