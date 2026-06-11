// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Per-node action rail. Renders the common-to-every-node action buttons
 * (Refresh / Branch / Branch-as-subtree / Delete / Open-in-linear) per
 * the operator-facing action surface; kind-specific actions (✏ edit,
 * ⚡ converter, etc.) defer to later sub-PRs.
 *
 * Callbacks are optional — a card mounted without any callbacks renders
 * an empty rail wrapper (PR5d's edge `+` chip relies on the wrapper for
 * anchor positioning). Each callback's presence opts in the corresponding
 * button; an undefined callback hides the button entirely. This keeps
 * the V1.0 enablement story incremental: PR5c lands the wiring; later
 * PRs add the actual runner calls behind each callback.
 */

import {
  ArrowSyncRegular,
  BranchForkRegular,
  BranchRegular,
  DeleteRegular,
  OpenRegular,
} from '@fluentui/react-icons'
import { Button, Tooltip } from '@fluentui/react-components'

import type { ConversationTreeNodeId } from '../../runner/treeTypes'
import { useActionRailStyles } from './actionRail.styles'

/**
 * Callback bag the host wires through TreeCanvas. Each callback is
 * optional — an undefined entry hides its button so PR5c can ship the
 * rail before every runner integration is wired.
 *
 * Callbacks receive the node id of the card they were fired on; PR5e+
 * may grow optional context arguments but the nodeId-first signature is
 * the stable invariant.
 */
export interface ActionCallbacks {
  onRefresh?: (nodeId: ConversationTreeNodeId) => void
  onBranch?: (nodeId: ConversationTreeNodeId) => void
  onDelete?: (nodeId: ConversationTreeNodeId) => void
  onOpenLinear?: (nodeId: ConversationTreeNodeId) => void
}

export interface ActionRailProps {
  nodeId: ConversationTreeNodeId
  callbacks: ActionCallbacks
  /**
   * Display text for the Branch button. "Clone tree" on a root node,
   * "Branch from here" elsewhere. The card chooses; the rail honors.
   */
  branchLabel: string
}

export function ActionRail({ nodeId, callbacks, branchLabel }: ActionRailProps) {
  const styles = useActionRailStyles()
  const { onRefresh, onBranch, onDelete, onOpenLinear } = callbacks
  return (
    <div data-tree-action-rail data-tree-node-id={nodeId} className={styles.rail}>
      {onRefresh !== undefined && (
        <Tooltip content="Refresh" relationship="description">
          <Button
            size="small"
            appearance="subtle"
            icon={<ArrowSyncRegular />}
            aria-label="Refresh"
            onClick={() => onRefresh(nodeId)}
          />
        </Tooltip>
      )}
      {onBranch !== undefined && (
        <Tooltip content={branchLabel} relationship="description">
          <Button
            size="small"
            appearance="subtle"
            icon={<BranchRegular />}
            aria-label={branchLabel}
            onClick={() => onBranch(nodeId)}
          />
        </Tooltip>
      )}
      {/*
        Branch-as-subtree is a V1.1 placeholder. Render disabled so the
        slot is reserved (operators don't get a new button surface
        appearing in V1.1 — only the disabled state flips). title-attr
        carries the tooltip per the disabled-button convention.
      */}
      <Button
        size="small"
        appearance="subtle"
        icon={<BranchForkRegular />}
        aria-label="Branch as subtree"
        title="Branch as subtree (coming in a future release)"
        disabled
      />
      {onDelete !== undefined && (
        <Tooltip content="Delete" relationship="description">
          <Button
            size="small"
            appearance="subtle"
            icon={<DeleteRegular />}
            aria-label="Delete"
            onClick={() => onDelete(nodeId)}
          />
        </Tooltip>
      )}
      {onOpenLinear !== undefined && (
        <Tooltip content="Open in linear view" relationship="description">
          <Button
            size="small"
            appearance="subtle"
            icon={<OpenRegular />}
            aria-label="Open in linear view"
            onClick={() => onOpenLinear(nodeId)}
          />
        </Tooltip>
      )}
    </div>
  )
}
