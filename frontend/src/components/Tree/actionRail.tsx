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
  CheckmarkCircleFilled,
  CheckmarkCircleRegular,
  DeleteRegular,
  OpenRegular,
} from '@fluentui/react-icons'
import { Button, Tooltip } from '@fluentui/react-components'

import type { ConversationTreeNodeId } from '../../runner/treeTypes'
import { useActionRailStyles } from './actionRail.styles'

/**
 * Discriminant for `onEdgeInsert` — names the operator's chosen insert
 * action so the host can dispatch the corresponding tree edit without
 * re-deriving "what would they want here" from the kind alone.
 *
 * V1.0 set (per the per-parent menu in PR5d's InsertEdge):
 *   - `follow_up_user_turn`   — UserTurn(role=user)
 *   - `inject_assistant_text` — UserTurn(role=simulated_assistant)
 *   - `send`                  — SendNode
 *   - `score`                 — ScoreNode
 *   - `append_converter`      — append to upstream UserTurn's converterPipeline
 *   - `fan_attempt`           — wrap edge target in FanNode(axis='attempt')
 *   - `fan_converter`         — wrap edge target in FanNode(axis='converter')
 *
 * V1.1 axes (`fan_prompt`, `fan_target`) reserve slot in the menu but
 * are disabled and not part of this enum; adding them is a non-breaking
 * V1.1 type extension.
 */
export type EdgeInsertKind =
  | 'follow_up_user_turn'
  | 'inject_assistant_text'
  | 'send'
  | 'score'
  | 'append_converter'
  | 'fan_attempt'
  | 'fan_converter'

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
  /**
   * Per-edge insert (PR5d). `parentId` is the source node of the edge,
   * `childId` the target, `kind` the operator's chosen insert action.
   * Host decides where in the tree the new node goes (typically:
   * splice between parent and child, attaching parent → new node →
   * child). When undefined, the per-edge `+` chip is suppressed.
   */
  onEdgeInsert?: (
    parentId: ConversationTreeNodeId,
    childId: ConversationTreeNodeId,
    kind: EdgeInsertKind,
  ) => void
  /**
   * Pick / Unpick a fan child (PR5f). `slotIndex` of the chosen child
   * (or `null` to unpick — clears the fan's promotedChildSlotIndex).
   * Host writes to `FanNode.params.promotedChildSlotIndex` and is
   * responsible for the auto-clear-on-child-delete invariant. When
   * undefined, the per-child Pick toggle AND the collapsed-stack
   * Pick popover are both suppressed.
   *
   * V1.0 is visual only: dim-non-promoted on the canvas. V1.1+ uses
   * the field to scope Refresh + Stack-edit.
   */
  onPickFanChild?: (
    fanNodeId: ConversationTreeNodeId,
    slotIndex: number | null,
  ) => void
}

export interface ActionRailProps {
  nodeId: ConversationTreeNodeId
  callbacks: ActionCallbacks
  /**
   * Display text for the Branch button. "Clone tree" on a root node,
   * "Branch from here" elsewhere. The card chooses; the rail honors.
   */
  branchLabel: string
  /**
   * When this card is a fan child, the parent fan id + slot index + the
   * current promoted state. When supplied AND `onPickFanChild` is wired,
   * the rail renders a CheckmarkCircle toggle button: outline = pickable,
   * filled = currently picked. Clicking toggles the slot (own slot when
   * unpicked → pick; own slot when promoted → unpick by passing null;
   * other slot promoted → switch to own slot).
   *
   * Absent for non-fan-children — no Pick affordance renders.
   */
  fanChildInfo?: {
    parentFanId: ConversationTreeNodeId
    slotIndex: number
    promoted: boolean
  }
}

export function ActionRail({ nodeId, callbacks, branchLabel, fanChildInfo }: ActionRailProps) {
  const styles = useActionRailStyles()
  const { onRefresh, onBranch, onDelete, onOpenLinear, onPickFanChild } = callbacks
  const showPick = fanChildInfo !== undefined && onPickFanChild !== undefined
  const onPickClick = () => {
    if (!showPick) return
    // Toggle semantics:
    //   - promoted (this slot is current pick) → unpick (null)
    //   - not promoted (no pick OR sibling pick) → switch to this slot
    const next = fanChildInfo.promoted ? null : fanChildInfo.slotIndex
    onPickFanChild(fanChildInfo.parentFanId, next)
  }
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
      {showPick && (
        <Tooltip
          content={fanChildInfo.promoted ? 'Unpick this attempt' : 'Pick this attempt'}
          relationship="description"
        >
          <Button
            size="small"
            appearance="subtle"
            icon={
              fanChildInfo.promoted ? <CheckmarkCircleFilled /> : <CheckmarkCircleRegular />
            }
            aria-label={fanChildInfo.promoted ? 'Unpick this attempt' : 'Pick this attempt'}
            onClick={onPickClick}
          />
        </Tooltip>
      )}
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
