// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * TreeRunnerHost — PR7b layout-only shell around `TreeCanvas`.
 *
 * Owns the flex layout for the V1.0 tree view. Five named slots:
 *   - `ribbon`  : canvas-top `WaveStatusRibbon` (idle in PR7b; wired
 *                 to a WaveEvent buffer in PR7c).
 *   - `canvas`  : `TreeCanvas` for the supplied tree (greenfield
 *                 placeholder when `tree === null`).
 *   - `drawer`  : right-side `PastRunsDrawer` slot (empty in PR7b).
 *   - `toast`   : bottom-right toast slot (empty in PR7b).
 *   - `modal`   : centred modal slot (empty in PR7b).
 *
 * No runner shim, no WaveEvent buffer, no modal hook — those land
 * in PR7c–d. `actionCallbacks` and `availableConverters` flow through
 * to TreeCanvas unchanged.
 */

import { TreeCanvas } from './TreeCanvas'
import { WaveStatusRibbon } from './WaveStatusRibbon'
import { useTreeRunnerHostStyles } from './TreeRunnerHost.styles'
import type { ActionCallbacks } from './actionRail'
import type { AvailableConvertersValue } from './availableConvertersContext'
import type { ConversationTree } from '../../runner/treeTypes'

export interface TreeRunnerHostProps {
  /** Foregrounded tree; `null` renders the greenfield placeholder. */
  tree: ConversationTree | null
  /** Pass-through to `TreeCanvas`. PR7c composes runner-shim-derived callbacks. */
  actionCallbacks?: ActionCallbacks
  /** Pass-through to `TreeCanvas`. PR7c may own the fetch. */
  availableConverters?: AvailableConvertersValue
}

export function TreeRunnerHost({
  tree,
  actionCallbacks,
  availableConverters,
}: TreeRunnerHostProps) {
  const styles = useTreeRunnerHostStyles()
  return (
    <div data-tree-runner-host className={styles.root}>
      <div data-slot="ribbon" className={styles.ribbon}>
        <WaveStatusRibbon state={{ status: 'idle' }} />
      </div>
      <div data-slot="canvas" className={styles.canvas}>
        {tree !== null ? (
          // Re-key on tree.id so react-flow's internal zoom/pan state
          // resets across tree swaps. TreeCanvas's own collapse state
          // is already re-keyed internally.
          <TreeCanvas
            key={tree.id}
            tree={tree}
            actionCallbacks={actionCallbacks}
            availableConverters={availableConverters}
          />
        ) : (
          <div data-tree-greenfield className={styles.greenfield}>
            <p>No tree loaded. Open one from history or start a new attack.</p>
          </div>
        )}
      </div>
      <div data-slot="drawer" className={styles.drawer} />
      <div data-slot="toast" className={styles.toast} />
      <div data-slot="modal" className={styles.modal} />
    </div>
  )
}
