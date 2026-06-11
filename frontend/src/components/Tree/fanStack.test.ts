// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Tests for the Fan-Children Stack render helpers.
 *
 * Pure tree-walker functions; no react, no react-flow, no DOM. Tests
 * exercise the predicate that decides when a fan is stackable, the
 * default-collapsed set (auto-collapse for N>3 stackable attempt-fans),
 * and the aggregate-status computation surfaced on the collapsed stack
 * card.
 *
 * Pinned contracts:
 *   - isStackable: parent is a FanNode AND axis is 'attempt' AND
 *     n >= 2 AND all children have structurally identical subtrees
 *   - defaultCollapsedFanIds: subset of stackable fans with N > 3
 *     (per the auto-collapse threshold in the design doc; smaller
 *     stackable fans render expanded by default but can be manually
 *     collapsed)
 *   - computeStackAggregate: count by lifecycle state across all
 *     stacked children, plus the child kind (always Send for V1.0
 *     attempt-axis but kept generic for future axes)
 */

import {
  computeStackAggregate,
  defaultCollapsedFanIds,
  isStackable,
  type StackAggregate,
} from './fanStack'
import {
  mkFan,
  mkRoot,
  mkSend,
  mkTree,
  mkUserTurn,
  nodeId,
} from '../../runner/testHelpers'
import type { FanVariant } from '../../runner/treeTypes'

function attemptVariants(n: number): FanVariant[] {
  return Array.from({ length: n }, () => ({ axis: 'attempt' as const, payload: {} }))
}

// ============================================================================
// isStackable
// ============================================================================

describe('isStackable', () => {
  it('returns true for an attempt-fan with 2+ structurally identical Send children', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f', 'u', { axis: 'attempt', variants: attemptVariants(2) }),
      mkSend('s_a', 'f'),
      mkSend('s_b', 'f'),
    ])
    expect(isStackable(tree, nodeId('f'))).toBe(true)
  })

  it('returns true for an attempt-fan with 10 isomorphic Send children', () => {
    const sends = Array.from({ length: 10 }, (_, i) => mkSend(`s_${i}`, 'f'))
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f', 'u', { axis: 'attempt', variants: attemptVariants(10) }),
      ...sends,
    ])
    expect(isStackable(tree, nodeId('f'))).toBe(true)
  })

  it('returns false when fan has only 1 child (degenerate)', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f', 'u', { axis: 'attempt', variants: attemptVariants(1) }),
      mkSend('s_a', 'f'),
    ])
    expect(isStackable(tree, nodeId('f'))).toBe(false)
  })

  it('returns false when fan has 0 children', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f', 'u', { axis: 'attempt', variants: [] }),
    ])
    expect(isStackable(tree, nodeId('f'))).toBe(false)
  })

  it('returns false for a converter-axis fan (only attempt produces collapsible stacks in V1.0)', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f', 'u', {
        axis: 'converter',
        variants: [
          { axis: 'converter', payload: { converters: [] } },
          { axis: 'converter', payload: { converters: [] } },
        ],
      }),
      mkSend('s_a', 'f'),
      mkSend('s_b', 'f'),
    ])
    expect(isStackable(tree, nodeId('f'))).toBe(false)
  })

  it('returns false when children have divergent subtree shapes (one has descendants, one does not)', () => {
    // s_a is a leaf Send; s_b has a UserTurn child — divergent shape.
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f', 'u', { axis: 'attempt', variants: attemptVariants(2) }),
      mkSend('s_a', 'f'),
      mkSend('s_b', 'f'),
      mkUserTurn('u_b', 's_b'),
    ])
    expect(isStackable(tree, nodeId('f'))).toBe(false)
  })

  it('returns false when children have divergent kinds (only attempt-fan should be all-Sends, but check the predicate)', () => {
    // Construct an unusual tree where one fan-child is a Send and another is a UserTurn.
    // mkTree wouldn't produce this from a real attempt-fan, but the predicate must guard.
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f', 'u', { axis: 'attempt', variants: attemptVariants(2) }),
      mkSend('s_a', 'f'),
      mkUserTurn('u_b', 'f'),
    ])
    expect(isStackable(tree, nodeId('f'))).toBe(false)
  })

  it('returns false for a non-fan node id', () => {
    const tree = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r'), mkSend('s', 'u')])
    expect(isStackable(tree, nodeId('u'))).toBe(false)
    expect(isStackable(tree, nodeId('s'))).toBe(false)
    expect(isStackable(tree, nodeId('r'))).toBe(false)
  })

  it('returns false for an unknown node id', () => {
    const tree = mkTree('r', [mkRoot('r')])
    expect(isStackable(tree, nodeId('ghost'))).toBe(false)
  })

  it('returns true for nested stackable fans (each evaluated independently)', () => {
    // Outer attempt-fan of 3 Send-leaves; one Send has a nested attempt-fan
    // of 3 Sends. Both fans are stackable in isolation.
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f_outer', 'u', { axis: 'attempt', variants: attemptVariants(3) }),
      mkSend('s_a', 'f_outer'),
      mkSend('s_b', 'f_outer'),
      mkSend('s_c', 'f_outer'),
    ])
    expect(isStackable(tree, nodeId('f_outer'))).toBe(true)
  })
})

// ============================================================================
// defaultCollapsedFanIds
// ============================================================================

describe('defaultCollapsedFanIds', () => {
  it('includes a stackable attempt-fan with N > 3 children', () => {
    const sends = Array.from({ length: 5 }, (_, i) => mkSend(`s_${i}`, 'f'))
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f', 'u', { axis: 'attempt', variants: attemptVariants(5) }),
      ...sends,
    ])
    expect(defaultCollapsedFanIds(tree).has(nodeId('f'))).toBe(true)
  })

  it('EXCLUDES a stackable attempt-fan with N = 2 (below auto-collapse threshold)', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f', 'u', { axis: 'attempt', variants: attemptVariants(2) }),
      mkSend('s_a', 'f'),
      mkSend('s_b', 'f'),
    ])
    expect(defaultCollapsedFanIds(tree).has(nodeId('f'))).toBe(false)
  })

  it('EXCLUDES a stackable attempt-fan with N = 3 (boundary; expanded by default)', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f', 'u', { axis: 'attempt', variants: attemptVariants(3) }),
      mkSend('s_a', 'f'),
      mkSend('s_b', 'f'),
      mkSend('s_c', 'f'),
    ])
    expect(defaultCollapsedFanIds(tree).has(nodeId('f'))).toBe(false)
  })

  it('EXCLUDES a non-stackable fan (converter axis, even with N > 3)', () => {
    const sends = Array.from({ length: 5 }, (_, i) => mkSend(`s_${i}`, 'f'))
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f', 'u', {
        axis: 'converter',
        variants: Array.from({ length: 5 }, () => ({ axis: 'converter' as const, payload: { converters: [] } })),
      }),
      ...sends,
    ])
    expect(defaultCollapsedFanIds(tree).has(nodeId('f'))).toBe(false)
  })

  it('returns empty for a tree with no fans', () => {
    const tree = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r'), mkSend('s', 'u')])
    expect(defaultCollapsedFanIds(tree).size).toBe(0)
  })

  it('includes multiple stackable fans in a tree', () => {
    // Two attempt-fans, both N=5, both stackable.
    const sends1 = Array.from({ length: 5 }, (_, i) => mkSend(`a_${i}`, 'f1'))
    const sends2 = Array.from({ length: 5 }, (_, i) => mkSend(`b_${i}`, 'f2'))
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u1', 'r'),
      mkFan('f1', 'u1', { axis: 'attempt', variants: attemptVariants(5) }),
      ...sends1,
      mkUserTurn('u2', 'r'),
      mkFan('f2', 'u2', { axis: 'attempt', variants: attemptVariants(5) }),
      ...sends2,
    ])
    const collapsed = defaultCollapsedFanIds(tree)
    expect(collapsed.has(nodeId('f1'))).toBe(true)
    expect(collapsed.has(nodeId('f2'))).toBe(true)
  })
})

// ============================================================================
// computeStackAggregate
// ============================================================================

describe('computeStackAggregate', () => {
  it('counts a fan with all clean children', () => {
    const sends = [
      mkSend('s_0', 'f', undefined, { state: 'clean' }),
      mkSend('s_1', 'f', undefined, { state: 'clean' }),
      mkSend('s_2', 'f', undefined, { state: 'clean' }),
    ]
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f', 'u', { axis: 'attempt', variants: attemptVariants(3) }),
      ...sends,
    ])
    const agg = computeStackAggregate(tree, nodeId('f'))
    expect(agg).toEqual<StackAggregate>({
      childKind: 'send',
      total: 3,
      byState: { clean: 3, edited: 0, stale: 0, running: 0, failed: 0, cancelled: 0, draft: 0 },
      members: [
        { id: nodeId('s_0'), slotIndex: 0, state: 'clean' },
        { id: nodeId('s_1'), slotIndex: 1, state: 'clean' },
        { id: nodeId('s_2'), slotIndex: 2, state: 'clean' },
      ],
    })
  })

  it('counts a fan with mixed states', () => {
    const sends = [
      mkSend('s_0', 'f', undefined, { state: 'clean' }),
      mkSend('s_1', 'f', undefined, { state: 'clean' }),
      mkSend('s_2', 'f', undefined, { state: 'running' }),
      mkSend('s_3', 'f', undefined, { state: 'failed' }),
      mkSend('s_4', 'f', undefined, { state: 'stale' }),
    ]
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f', 'u', { axis: 'attempt', variants: attemptVariants(5) }),
      ...sends,
    ])
    const agg = computeStackAggregate(tree, nodeId('f'))
    expect(agg).toEqual<StackAggregate>({
      childKind: 'send',
      total: 5,
      byState: { clean: 2, edited: 0, stale: 1, running: 1, failed: 1, cancelled: 0, draft: 0 },
      members: [
        { id: nodeId('s_0'), slotIndex: 0, state: 'clean' },
        { id: nodeId('s_1'), slotIndex: 1, state: 'clean' },
        { id: nodeId('s_2'), slotIndex: 2, state: 'running' },
        { id: nodeId('s_3'), slotIndex: 3, state: 'failed' },
        { id: nodeId('s_4'), slotIndex: 4, state: 'stale' },
      ],
    })
  })

  it("childKind is null when fan has no children (degenerate; predicate would reject anyway)", () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f', 'u', { axis: 'attempt', variants: [] }),
    ])
    const agg = computeStackAggregate(tree, nodeId('f'))
    expect(agg.total).toBe(0)
    expect(agg.childKind).toBeNull()
  })

  it('returns total=0 for a non-fan node', () => {
    const tree = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r')])
    const agg = computeStackAggregate(tree, nodeId('u'))
    expect(agg.total).toBe(0)
    expect(agg.childKind).toBeNull()
  })
})
