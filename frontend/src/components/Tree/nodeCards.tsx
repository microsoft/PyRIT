// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Per-kind node card components for the react-flow canvas.
 *
 * Each card is the visual representation of one ConversationTreeNode kind.
 * Cards are read-only display in PR5b — the action rail (PR5c), edge `+`
 * chip (PR5d), Stack rendering (PR5e), Pick/Unpick (PR5f), and layout
 * (PR5g) land separately.
 *
 * Cards thread the `selected` prop react-flow passes to every node
 * component so PR5c's action-rail visibility can read it; selection
 * visual (brand-color outline) lives in nodeCards.styles.ts and is
 * applied on every card today via the shared CardFrame.
 */

import { mergeClasses } from '@fluentui/react-components'
import { Handle, Position } from '@xyflow/react'
import type { NodeProps } from '@xyflow/react'

import type {
  ConversationTreeNodeId,
  FanNode,
  ImportMessageNode,
  NodeState,
  RootPromptNode,
  ScoreNode,
  SendNode,
  UserTurnNode,
} from '../../runner/treeTypes'
import { ActionRail } from './actionRail'
import { useActionCallbacks } from './actionCallbacksContext'
import type { TreeFlowNode } from './conversationTreeToReactFlow'
import { STATE_BADGE_TOKENS, useNodeCardStyles } from './nodeCards.styles'

// ============================================================================
// Shared building blocks
// ============================================================================

interface CardFrameProps {
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
  showTargetHandle?: boolean // top (parent connection)
  showSourceHandle?: boolean // bottom (child connection)
  children: React.ReactNode
}

function CardFrame({
  kindLabel,
  state,
  nodeId,
  selected = false,
  branchLabel,
  showTargetHandle = true,
  showSourceHandle = true,
  children,
}: CardFrameProps) {
  const styles = useNodeCardStyles()
  const callbacks = useActionCallbacks()
  const stateTokens = STATE_BADGE_TOKENS[state]
  return (
    <div
      data-tree-node-id={nodeId}
      data-selected={selected ? 'true' : 'false'}
      className={mergeClasses(styles.frame, selected && styles.frameSelected)}
    >
      {showTargetHandle && <Handle type="target" position={Position.Top} />}
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
        <ActionRail nodeId={nodeId} callbacks={callbacks} branchLabel={branchLabel} />
      )}
      {showSourceHandle && <Handle type="source" position={Position.Bottom} />}
    </div>
  )
}

function CardBody({ text }: { text: string }) {
  const styles = useNodeCardStyles()
  return (
    <div data-testid="node-body" title={text} className={styles.body}>
      {text}
    </div>
  )
}

function MetaRow({ label, value }: { label: string; value: string }) {
  const styles = useNodeCardStyles()
  return (
    <div className={styles.metaRow}>
      {label !== '' && <span className={styles.metaLabel}>{label}:</span>}
      <span className={styles.metaValue}>{value}</span>
    </div>
  )
}

// ============================================================================
// RootPromptCard
// ============================================================================

type RootPromptProps = NodeProps<Extract<TreeFlowNode, { type: 'root_prompt' }>>

export function RootPromptCard({ data, selected }: RootPromptProps) {
  const node: RootPromptNode = data.node
  return (
    <CardFrame
      kindLabel="Root prompt"
      state={node.state}
      nodeId={node.id}
      selected={selected}
      branchLabel="Clone tree"
      showTargetHandle={false}
    >
      <CardBody text={node.params.text} />
      <MetaRow label="target" value={node.params.targetRegistryName} />
    </CardFrame>
  )
}

// ============================================================================
// ImportMessageCard
// ============================================================================

type ImportMessageProps = NodeProps<Extract<TreeFlowNode, { type: 'import_message' }>>

export function ImportMessageCard({ data, selected }: ImportMessageProps) {
  const node: ImportMessageNode = data.node
  return (
    <CardFrame
      kindLabel="Imported message"
      state={node.state}
      nodeId={node.id}
      selected={selected}
      branchLabel="Branch from here"
      showTargetHandle={false}
    >
      <MetaRow label="source" value={node.params.sourceConversationId} />
      <MetaRow label="cutoff" value={String(node.params.cutoffIndex)} />
    </CardFrame>
  )
}

// ============================================================================
// UserTurnCard
// ============================================================================

type UserTurnProps = NodeProps<Extract<TreeFlowNode, { type: 'user_turn' }>>

export function UserTurnCard({ data, selected }: UserTurnProps) {
  const node: UserTurnNode = data.node
  const converters = node.params.converterPipeline ?? []
  return (
    <CardFrame
      kindLabel="User turn"
      state={node.state}
      nodeId={node.id}
      selected={selected}
      branchLabel="Branch from here"
    >
      <CardBody text={node.params.text} />
      <MetaRow label="role" value={node.params.role} />
      {converters.length > 0 && (
        <MetaRow label="" value={`${converters.length} converter${converters.length === 1 ? '' : 's'}`} />
      )}
    </CardFrame>
  )
}

// ============================================================================
// SendCard
// ============================================================================

type SendProps = NodeProps<Extract<TreeFlowNode, { type: 'send' }>>

export function SendCard({ data, selected }: SendProps) {
  const node: SendNode = data.node
  const styles = useNodeCardStyles()
  return (
    <CardFrame
      kindLabel="Send"
      state={node.state}
      nodeId={node.id}
      selected={selected}
      branchLabel="Branch from here"
    >
      {node.params.targetRegistryName !== undefined && (
        <MetaRow label="target" value={node.params.targetRegistryName} />
      )}
      {node.state === 'failed' && node.lastError !== null && (
        <div className={styles.errorPanel}>{node.lastError.message}</div>
      )}
    </CardFrame>
  )
}

// ============================================================================
// FanCard
// ============================================================================

type FanProps = NodeProps<Extract<TreeFlowNode, { type: 'fan' }>>

export function FanCard({ data, selected }: FanProps) {
  const node: FanNode = data.node
  const n = node.params.variants.length
  return (
    <CardFrame
      kindLabel="Fan"
      state={node.state}
      nodeId={node.id}
      selected={selected}
      branchLabel="Branch from here"
    >
      <MetaRow label="axis" value={node.params.axis} />
      <MetaRow label="" value={`${n} variant${n === 1 ? '' : 's'}`} />
      {node.params.promotedChildSlotIndex !== null && (
        <MetaRow label="pick" value={`slot ${node.params.promotedChildSlotIndex}`} />
      )}
    </CardFrame>
  )
}

// ============================================================================
// ScoreCard
// ============================================================================

type ScoreProps = NodeProps<Extract<TreeFlowNode, { type: 'score' }>>

export function ScoreCard({ data, selected }: ScoreProps) {
  const node: ScoreNode = data.node
  const styles = useNodeCardStyles()
  return (
    <CardFrame
      kindLabel="Score"
      state={node.state}
      nodeId={node.id}
      selected={selected}
      branchLabel="Branch from here"
    >
      <MetaRow label="scorer" value={node.params.scorerType} />
      <div className={styles.mutedFooter}>Read-only display</div>
    </CardFrame>
  )
}
