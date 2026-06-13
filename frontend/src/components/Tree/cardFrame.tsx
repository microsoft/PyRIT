// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { mergeClasses } from '@fluentui/react-components'
import { Handle, Position } from '@xyflow/react'

import type {
  ConversationTreeNodeId,
  NodeState,
} from '../../runner/treeTypes'
import { ActionRail } from './actionRail'
import { useActionCallbacks } from './actionCallbacksContext'
import { STATE_BADGE_TOKENS, useNodeCardStyles } from './nodeCards.styles'
import type { FanChildInfo } from './conversationTreeToReactFlow'

export interface CardFrameProps {
  kindLabel: string
  state: NodeState
  nodeId: ConversationTreeNodeId
  /**
   * Selection state from react-flow's NodeProps. Optional because
   * react-flow types it `boolean | undefined`; CardFrame is the one
   * place that defaults to `false` so cards don't repeat the fallback.
   */
  selected?: boolean
  /**
   * Display text for the action-rail Branch button. "Clone tree" on a
   * root node, "Branch from here" elsewhere. Required when callbacks
   * are present; ignored when they're absent (no rail renders).
   */
  branchLabel: string
  /**
   * PR5f: forwarded from the card's adapter-supplied data. CardFrame
   * applies the dim/promoted CSS class + emits data-dimmed/data-promoted
   * attributes, AND threads the fan context into the ActionRail so the
   * Pick toggle has what it needs.
   */
  fanChildInfo?: FanChildInfo
  /**
   * Kind-specific action buttons (PR5h.5+). Cards render their own
   * per-kind icons here; CardFrame forwards them to ActionRail where
   * they render alongside the common Refresh/Branch/etc. icons. Absent
   * for cards with no per-kind actions in V1.0.
   */
  kindActions?: React.ReactNode
  canDelete?: boolean
  showTargetHandle?: boolean // top (parent connection)
  showSourceHandle?: boolean // bottom (child connection)
  children: React.ReactNode
}

export function CardFrame({
  kindLabel,
  state,
  nodeId,
  selected = false,
  branchLabel,
  fanChildInfo,
  kindActions,
  canDelete = true,
  showTargetHandle = true,
  showSourceHandle = true,
  children,
}: CardFrameProps) {
  const styles = useNodeCardStyles()
  const callbacks = useActionCallbacks()
  const stateTokens = STATE_BADGE_TOKENS[state]
  const dimmed = fanChildInfo?.dimmed ?? false
  const promoted = fanChildInfo?.promoted ?? false
  return (
    <div
      data-tree-node-id={nodeId}
      data-selected={selected ? 'true' : 'false'}
      data-dimmed={dimmed ? 'true' : 'false'}
      data-promoted={promoted ? 'true' : 'false'}
      className={mergeClasses(
        styles.frame,
        selected && styles.frameSelected,
        dimmed && styles.frameDimmed,
        promoted && styles.framePromoted,
      )}
    >
      {showTargetHandle && (
        <Handle
          type="target"
          position={Position.Top}
          className={mergeClasses(styles.handle, styles.handleTarget)}
          data-tree-node-handle="target"
        />
      )}
      <div className={styles.header}>
        <span className={styles.kindLabel}>{kindLabel}</span>
        <span
          data-testid={`node-state-${nodeId}`}
          className={styles.stateBadge}
          style={{ background: stateTokens.background, color: stateTokens.foreground }}
        >
          {state}
        </span>
      </div>
      {children}
      {callbacks !== null && (
        <ActionRail
          nodeId={nodeId}
          callbacks={callbacks}
          branchLabel={branchLabel}
          canDelete={canDelete}
          fanChildInfo={fanChildInfo}
          kindActions={kindActions}
        />
      )}
      {showSourceHandle && (
        <Handle
          type="source"
          position={Position.Bottom}
          className={mergeClasses(styles.handle, styles.handleSource)}
          data-tree-node-handle="source"
        />
      )}
    </div>
  )
}

export function CardBody({ text }: { text: string }) {
  const styles = useNodeCardStyles()
  return (
    <div data-testid="node-body" title={text} className={styles.body}>
      {text}
    </div>
  )
}

export function MetaRow({
  label,
  value,
  title,
}: {
  label: string
  value: string
  /** Optional hover-discoverable tooltip; surfaces via the row's HTML title attr. */
  title?: string
}) {
  const styles = useNodeCardStyles()
  return (
    <div className={styles.metaRow} title={title}>
      {label !== '' && <span className={styles.metaLabel}>{label}:</span>}
      <span className={styles.metaValue}>{value}</span>
    </div>
  )
}
