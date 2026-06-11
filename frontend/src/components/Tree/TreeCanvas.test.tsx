// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Tests for `TreeCanvas` — the react-flow scaffold component that mounts
 * a ConversationTree as a graph.
 *
 * Scope (PR5a): the scaffold accepts a ConversationTree, hands it to the
 * adapter, and mounts ReactFlow with the resulting nodes/edges. Per-node
 * components register in PR5b; layout positions land in PR5g.
 *
 * What this pins:
 *   - one DOM node per ConversationTreeNode (react-flow renders each as a
 *     `[data-id="<nodeId>"]` element via the default node component)
 *   - the `treeId` prop scopes the canvas to one tree (PR5b+ will use it
 *     to route action-rail callbacks back to the runner shim)
 *   - the canvas survives a tree-prop swap without remount (react-flow's
 *     reconciler keys on node id, so identity-stable ids matter — adapter
 *     guarantees this)
 *
 * NOT in scope here:
 *   - per-node component rendering (PR5b) — tests assert react-flow's
 *     default-node text content, which is the node id
 *   - layout (PR5g) — every node renders at the origin; visual overlap is
 *     expected, the test doesn't read positions
 *   - interactivity (action rail, edge `+` chip — PR5b-d)
 */

import { render, screen } from '@testing-library/react'

import { TreeCanvas } from './TreeCanvas'
import {
  mkFan,
  mkRoot,
  mkSend,
  mkTree,
  mkUserTurn,
  treeId,
} from '../../runner/testHelpers'

// jsdom doesn't implement ResizeObserver beyond the setupTests.ts mock; that
// mock returns observers that no-op. react-flow's measurement code tolerates
// that — nodes mount with width/height 0 but the DOM elements still render.

describe('TreeCanvas — scaffold mount', () => {
  it('renders one node card per ConversationTreeNode', () => {
    const tree = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r'), mkSend('s', 'u')])
    const { container } = render(<TreeCanvas tree={tree} />)
    // CardFrame emits `data-tree-node-id` on each card's wrapper div.
    // This selector is under our control, not coupled to react-flow's
    // internal testid scheme (`rf__node-*` is private API).
    const nodeEls = container.querySelectorAll('[data-tree-node-id]')
    const ids = Array.from(nodeEls).map((el) => el.getAttribute('data-tree-node-id'))
    expect(ids.sort()).toEqual(['r', 's', 'u'])
  })

  it('renders the treeId as a stable attribute on the canvas wrapper', () => {
    // PR5b+ wires action-rail callbacks back to the runner shim using the
    // treeId; surfacing it on a data attribute makes it test-introspectable
    // without exposing a useRef + imperative handle.
    const tree = mkTree('r', [mkRoot('r')], { id: 't-canvas' })
    render(<TreeCanvas tree={tree} />)
    const wrapper = screen.getByTestId('tree-canvas')
    expect(wrapper.getAttribute('data-tree-id')).toBe(treeId('t-canvas'))
  })

  it('survives a tree-swap re-render without losing the node count', () => {
    const tree1 = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r')])
    const tree2 = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r'), mkSend('s', 'u')])
    const { container, rerender } = render(<TreeCanvas tree={tree1} />)
    expect(container.querySelectorAll('[data-tree-node-id]')).toHaveLength(2)
    rerender(<TreeCanvas tree={tree2} />)
    expect(container.querySelectorAll('[data-tree-node-id]')).toHaveLength(3)
  })

  it('renders a wide tree with multiple fan-children paths', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f', 'u', {
        axis: 'attempt',
        variants: [
          { axis: 'attempt', payload: {} },
          { axis: 'attempt', payload: {} },
          { axis: 'attempt', payload: {} },
        ],
      }),
      mkSend('s_a', 'f'),
      mkSend('s_b', 'f'),
      mkSend('s_c', 'f'),
    ])
    const { container } = render(<TreeCanvas tree={tree} />)
    expect(container.querySelectorAll('[data-tree-node-id]')).toHaveLength(6)
  })
})

// ============================================================================
// Layout memoization — PR5h.1
// ============================================================================
//
// The reviewer's bundle B+D: layout must memoize on shape (node ids + edge
// ids), NOT on the adapter output reference. A PR6-era wave that flips
// `node.state` from `running` → `clean` creates new tree refs but does not
// alter shape; layout must NOT re-run, otherwise a 60-leaf wave re-layouts
// 60 times.

import * as layoutModule from './layoutTree'

describe('TreeCanvas — layout memoization (shape-key cache)', () => {
  beforeEach(() => {
    jest.restoreAllMocks()
  })

  it('layoutTree runs once across multiple state-only re-renders (shape unchanged)', () => {
    const layoutSpy = jest.spyOn(layoutModule, 'layoutTree')
    const tree1 = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkSend('s', 'u', undefined, { state: 'clean' }),
    ])
    const { rerender } = render(<TreeCanvas tree={tree1} />)
    const callsAfterFirstRender = layoutSpy.mock.calls.length
    expect(callsAfterFirstRender).toBeGreaterThanOrEqual(1)

    // Simulate a state flip: same shape, new tree ref, only the Send's
    // state changes (clean → running). The cache should return the
    // previous positions and NOT call layoutTree again.
    const tree2 = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkSend('s', 'u', undefined, { state: 'running' }),
    ], { id: tree1.id })
    rerender(<TreeCanvas tree={tree2} />)
    const tree3 = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkSend('s', 'u', undefined, { state: 'clean' }),
    ], { id: tree1.id })
    rerender(<TreeCanvas tree={tree3} />)

    expect(layoutSpy.mock.calls.length).toBe(callsAfterFirstRender)
  })

  it('layoutTree re-runs when shape changes (a node is added)', () => {
    const layoutSpy = jest.spyOn(layoutModule, 'layoutTree')
    const tree1 = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r')])
    const { rerender } = render(<TreeCanvas tree={tree1} />)
    const callsAfterFirstRender = layoutSpy.mock.calls.length

    const tree2 = mkTree(
      'r',
      [mkRoot('r'), mkUserTurn('u', 'r'), mkSend('s', 'u')],
      { id: tree1.id },
    )
    rerender(<TreeCanvas tree={tree2} />)

    expect(layoutSpy.mock.calls.length).toBeGreaterThan(callsAfterFirstRender)
  })
})
