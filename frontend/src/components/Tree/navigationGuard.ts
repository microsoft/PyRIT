// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Pure navigation-guard decision for the tree view (spec §13.1a).
 *
 * When the operator navigates away from the tree view, route the view
 * change through the tree's dirty-edit `guardedSwap` so unrefreshed
 * edits prompt a confirm. All other transitions (into/within the tree,
 * or when no guard is mounted) navigate directly. Kept pure so App.tsx's
 * navigation glue stays thin + this decision is unit-tested.
 */

import type { ConversationTree } from '../../runner/treeTypes'
import type { ViewName } from '../Sidebar/Navigation'

export interface GuardedNavigateArgs {
  currentView: ViewName
  target: ViewName
  /** The foregrounded tree (checked for unrefreshed edits by the guard). */
  tree: ConversationTree | null
  /** The host's dirty-edit guard; null when the tree host isn't mounted. */
  guardedSwap: ((tree: ConversationTree | null, swap: () => void) => void) | null
  navigate: (view: ViewName) => void
}

export function guardedNavigate({
  currentView,
  target,
  tree,
  guardedSwap,
  navigate,
}: GuardedNavigateArgs): void {
  if (currentView === 'tree' && target !== 'tree' && guardedSwap !== null) {
    guardedSwap(tree, () => navigate(target))
    return
  }
  navigate(target)
}
