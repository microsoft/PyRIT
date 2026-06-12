// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Tests for `TreeRunnerHost` — the PR7 host shell that owns the flex
 * layout around TreeCanvas. PR7b ships layout only: 5 named slots
 * (ribbon / canvas / drawer / toast / modal), an always-idle wave-
 * status ribbon, and a greenfield placeholder when `tree === null`.
 * No runner shim, no WaveEvent buffer, no modal hook — those land in
 * PR7c–d.
 */

import { render } from '@testing-library/react'

import { TreeRunnerHost } from './TreeRunnerHost'
import type { ConversationTree, ConversationTreeId } from '../../runner/treeTypes'
import { treeId, nodeId } from '../../runner/testHelpers'

// ============================================================================
// Fixture
// ============================================================================

function mkEmptyTree(id: string): ConversationTree {
  const rootId = nodeId(`${id}-root`)
  return {
    id: treeId(id) as ConversationTreeId,
    nodes: [
      {
        id: rootId,
        kind: 'root_prompt',
        parentId: null,
        resolvedInputHash: '',
        state: 'clean',
        execution: null,
        executionHistory: [],
        lastError: null,
        labels: {},
        createdAt: '2026-06-11T00:00:00Z',
        updatedAt: '2026-06-11T00:00:00Z',
        version: 1,
        params: { text: 'hi', attachments: [], targetRegistryName: '' },
      },
    ],
    edges: [],
    rootId,
    displayName: id,
    createdAt: '2026-06-11T00:00:00Z',
    parentConversationTreeId: null,
    parentSourceConversationId: null,
    undoStack: [],
  }
}

// ============================================================================
// Layout — 5 named slots
// ============================================================================

describe('TreeRunnerHost — layout slots', () => {
  it('renders all 5 named slots (ribbon, canvas, drawer, toast, modal)', () => {
    const { container } = render(<TreeRunnerHost tree={null} />)
    expect(container.querySelector('[data-tree-runner-host]')).not.toBeNull()
    expect(container.querySelector('[data-slot="ribbon"]')).not.toBeNull()
    expect(container.querySelector('[data-slot="canvas"]')).not.toBeNull()
    expect(container.querySelector('[data-slot="drawer"]')).not.toBeNull()
    expect(container.querySelector('[data-slot="toast"]')).not.toBeNull()
    expect(container.querySelector('[data-slot="modal"]')).not.toBeNull()
  })
})

// ============================================================================
// Idle ribbon — always rendered (in-flight UI absent in PR7b)
// ============================================================================

describe('TreeRunnerHost — ribbon', () => {
  it('renders the wave-status ribbon wrapper in idle state', () => {
    const { container } = render(<TreeRunnerHost tree={null} />)
    const ribbon = container.querySelector('[data-tree-wave-status]')
    expect(ribbon).not.toBeNull()
    expect(ribbon?.getAttribute('data-status')).toBe('idle')
  })
})

// ============================================================================
// Greenfield — tree === null
// ============================================================================

describe('TreeRunnerHost — greenfield placeholder', () => {
  it('renders a greenfield placeholder when tree is null', () => {
    const { container } = render(<TreeRunnerHost tree={null} />)
    const placeholder = container.querySelector('[data-tree-greenfield]')
    expect(placeholder).not.toBeNull()
    // Operator-friendly message; copy is asserted loosely so PR7c+ can
    // tighten without re-flowing the test.
    expect(placeholder?.textContent?.toLowerCase()).toMatch(/no tree|empty|open/)
  })

  it('does NOT render a TreeCanvas when tree is null', () => {
    const { container } = render(<TreeRunnerHost tree={null} />)
    expect(container.querySelector('[data-testid="tree-canvas"]')).toBeNull()
  })
})

// ============================================================================
// Tree mount + swap re-key
// ============================================================================

describe('TreeRunnerHost — tree mount', () => {
  it('mounts a TreeCanvas with the supplied tree id when tree is not null', () => {
    const tree = mkEmptyTree('t-1')
    const { container } = render(<TreeRunnerHost tree={tree} />)
    const canvas = container.querySelector('[data-testid="tree-canvas"]')
    expect(canvas).not.toBeNull()
    expect(canvas?.getAttribute('data-tree-id')).toBe('t-1')
    // No greenfield placeholder when a tree is mounted.
    expect(container.querySelector('[data-tree-greenfield]')).toBeNull()
  })

  it('re-renders the canvas with the new tree id on tree swap', () => {
    const treeA = mkEmptyTree('t-A')
    const treeB = mkEmptyTree('t-B')
    const { container, rerender } = render(<TreeRunnerHost tree={treeA} />)
    expect(container.querySelector('[data-testid="tree-canvas"]')?.getAttribute('data-tree-id')).toBe('t-A')
    rerender(<TreeRunnerHost tree={treeB} />)
    expect(container.querySelector('[data-testid="tree-canvas"]')?.getAttribute('data-tree-id')).toBe('t-B')
  })
})
