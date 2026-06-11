// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * React context for the Fan-Children Stack collapse state.
 *
 * TreeCanvas owns the collapsed-fan set (per-mount, seeded from
 * `defaultCollapsedFanIds(tree)`); the FanCard reads its own collapse
 * state from the context and invokes the toggle callback when the
 * operator clicks the ⊞ / ⊟ button.
 *
 * The context value is null by default so cards rendered outside a
 * TreeCanvas (per-card tests) skip the collapse logic and render the
 * normal body. This mirrors the pattern from ActionCallbacksContext —
 * one source of truth for "is this UI surface live or not."
 */

import { createContext, useContext } from 'react'

import type { ConversationTreeNodeId } from '../../runner/treeTypes'

export interface StackCollapseValue {
  /** The set of fan-node ids currently rendered as a collapsed stack. */
  collapsedFanIds: ReadonlySet<ConversationTreeNodeId>
  /** Flip the collapse state for the given fan id. */
  toggleStack: (fanNodeId: ConversationTreeNodeId) => void
}

export const StackCollapseContext = createContext<StackCollapseValue | null>(null)

export function useStackCollapse(): StackCollapseValue | null {
  return useContext(StackCollapseContext)
}
