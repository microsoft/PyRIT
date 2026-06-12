// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Pure tree-state reducer functions per spec 01 §6.4–6.6.
 *
 * Bridges the runner's `RunnerStateSink` interface to the host's
 * React-state `ConversationTree`. Each reducer takes a tree + args
 * and returns a new tree (immutable update, structural sharing for
 * unchanged nodes). The host's sink methods compose these with
 * `setState`.
 *
 * Missing-node tolerance: every reducer returns the same tree
 * reference when the target node does not exist, matching the
 * sink's silent-no-op contract for operator-deleted-mid-wave races
 * (spec 01 §2.2).
 */

import type {
  ApiErrorReason,
  ConversationTree,
  ConversationTreeNode,
  ConversationTreeNodeId,
  ExecutionRecord,
  NodeState,
  ReflogEntry,
} from './treeTypes'

const DEFAULT_REFLOG_CAP = 50

function nowIso(): string {
  return new Date().toISOString()
}

function normalizeReason(reason: string | ApiErrorReason): ApiErrorReason {
  if (typeof reason === 'string') {
    return { message: reason, failure_class: 'transient' }
  }
  return reason
}

function bumpBase<N extends ConversationTreeNode>(node: N): N {
  return {
    ...node,
    updatedAt: nowIso(),
    version: node.version + 1,
  }
}

function replaceNode(
  tree: ConversationTree,
  nodeId: ConversationTreeNodeId,
  mutate: (node: ConversationTreeNode) => ConversationTreeNode | null,
): ConversationTree {
  const idx = tree.nodes.findIndex((n) => n.id === nodeId)
  if (idx === -1) return tree
  const next = mutate(tree.nodes[idx])
  if (next === null) return tree
  const nodes = tree.nodes.slice()
  nodes[idx] = next
  return { ...tree, nodes }
}

// ============================================================================
// applySetNodeState
// ============================================================================

export interface SetNodeStateOpts {
  reason?: string | ApiErrorReason | null
}

export function applySetNodeState(
  tree: ConversationTree,
  nodeId: ConversationTreeNodeId,
  state: NodeState,
  opts: SetNodeStateOpts = {},
): ConversationTree {
  return replaceNode(tree, nodeId, (node) => {
    let lastError = node.lastError
    if (opts.reason === null) {
      lastError = null
    } else if (opts.reason !== undefined) {
      lastError = normalizeReason(opts.reason)
    }
    return {
      ...bumpBase(node),
      state,
      lastError,
    }
  })
}

// ============================================================================
// applyRecordExecution
// ============================================================================

export interface RecordExecutionOpts {
  reflogCap?: number
}

export function applyRecordExecution(
  tree: ConversationTree,
  nodeId: ConversationTreeNodeId,
  record: ExecutionRecord,
  opts: RecordExecutionOpts = {},
): ConversationTree {
  const cap = opts.reflogCap ?? DEFAULT_REFLOG_CAP
  return replaceNode(tree, nodeId, (node) => {
    const prior = node.execution
    let history = node.executionHistory
    if (prior !== null) {
      const entry: ReflogEntry = { execution: prior, pinned: false }
      history = trimReflog([entry, ...history], cap)
    }
    return {
      ...bumpBase(node),
      execution: record,
      executionHistory: history,
    }
  })
}

function trimReflog(entries: ReflogEntry[], cap: number): ReflogEntry[] {
  if (entries.length <= cap) return entries
  // Evict oldest unpinned entries from the tail until the list fits the cap.
  // Pinned entries survive even past the cap (spec §6.6).
  const out = entries.slice()
  for (let i = out.length - 1; i >= 0 && out.length > cap; i -= 1) {
    if (!out[i].pinned) out.splice(i, 1)
  }
  return out
}

// ============================================================================
// applyClearExecution
// ============================================================================

export function applyClearExecution(
  tree: ConversationTree,
  nodeId: ConversationTreeNodeId,
): ConversationTree {
  return replaceNode(tree, nodeId, (node) => ({
    ...bumpBase(node),
    execution: null,
  }))
}

// ============================================================================
// applySetReflogPinned
// ============================================================================

export function applySetReflogPinned(
  tree: ConversationTree,
  nodeId: ConversationTreeNodeId,
  executionId: string,
  pinned: boolean,
): ConversationTree {
  const idx = tree.nodes.findIndex((n) => n.id === nodeId)
  if (idx === -1) return tree
  const node = tree.nodes[idx]
  const entryIdx = node.executionHistory.findIndex(
    (e) => e.execution.executionId === executionId,
  )
  if (entryIdx === -1) return tree
  if (node.executionHistory[entryIdx].pinned === pinned) return tree
  const history = node.executionHistory.slice()
  history[entryIdx] = { ...history[entryIdx], pinned }
  const nodes = tree.nodes.slice()
  nodes[idx] = { ...bumpBase(node), executionHistory: history }
  return { ...tree, nodes }
}
