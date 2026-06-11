// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Per-kind node card components + registry for the react-flow canvas.
 *
 * Each card is the visual representation of one ConversationTreeNode kind.
 * Cards are read-only display in PR5b — the action rail (PR5c), edge `+`
 * chip (PR5d), Stack rendering (PR5e), Pick/Unpick (PR5f), and layout
 * (PR5g) land separately.
 *
 * Backed by:
 * - 02 §2 (per-kind card content)
 * - 02 §2.3 (state badge)
 * - 02 §3.1 (Fan-Children Stack — only the Pick indicator lands here)
 */

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
import type { TreeFlowNode } from './conversationTreeToReactFlow'

// ============================================================================
// Shared building blocks
// ============================================================================

interface CardFrameProps {
  kindLabel: string
  state: NodeState
  nodeId: ConversationTreeNodeId
  showTargetHandle?: boolean // top (parent connection)
  showSourceHandle?: boolean // bottom (child connection)
  children: React.ReactNode
}

const STATE_COLORS: Record<NodeState, { background: string; foreground: string }> = {
  draft: { background: '#3a3a3a', foreground: '#e0e0e0' },
  clean: { background: '#1e3a1e', foreground: '#a0e0a0' },
  edited: { background: '#3a3a1e', foreground: '#e0d080' },
  stale: { background: '#3a2a1e', foreground: '#e0b080' },
  running: { background: '#1e2a3a', foreground: '#80b0e0' },
  failed: { background: '#3a1e1e', foreground: '#e08080' },
  cancelled: { background: '#2a2a2a', foreground: '#a0a0a0' },
}

function CardFrame({
  kindLabel,
  state,
  nodeId,
  showTargetHandle = true,
  showSourceHandle = true,
  children,
}: CardFrameProps) {
  const stateStyle = STATE_COLORS[state]
  return (
    <div
      style={{
        background: '#1c1c1c',
        color: '#e0e0e0',
        border: '1px solid #3a3a3a',
        borderRadius: 6,
        padding: '8px 12px',
        minWidth: 220,
        maxWidth: 320,
        fontFamily: 'system-ui, sans-serif',
        fontSize: 12,
      }}
    >
      {showTargetHandle && <Handle type="target" position={Position.Top} />}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
        <span style={{ fontWeight: 600, fontSize: 11, opacity: 0.7, textTransform: 'uppercase', letterSpacing: 0.5 }}>
          {kindLabel}
        </span>
        <span
          data-testid={`node-state-${nodeId}`}
          style={{
            background: stateStyle.background,
            color: stateStyle.foreground,
            padding: '1px 6px',
            borderRadius: 3,
            fontSize: 10,
            fontWeight: 500,
            textTransform: 'lowercase',
          }}
        >
          {state}
        </span>
      </div>
      {children}
      {showSourceHandle && <Handle type="source" position={Position.Bottom} />}
    </div>
  )
}

interface BodyProps {
  text: string
  maxLines?: number
}

function CardBody({ text, maxLines = 4 }: BodyProps) {
  return (
    <div
      data-testid="node-body"
      title={text}
      style={{
        display: '-webkit-box',
        WebkitLineClamp: maxLines,
        WebkitBoxOrient: 'vertical',
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'pre-wrap',
        lineHeight: 1.35,
      }}
    >
      {text}
    </div>
  )
}

function MetaRow({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: 'flex', gap: 6, marginTop: 4, fontSize: 11, opacity: 0.85 }}>
      <span style={{ opacity: 0.6 }}>{label}:</span>
      <span style={{ fontFamily: 'monospace' }}>{value}</span>
    </div>
  )
}

// ============================================================================
// RootPromptCard
// ============================================================================

type RootPromptProps = NodeProps<Extract<TreeFlowNode, { type: 'root_prompt' }>>

export function RootPromptCard({ data }: RootPromptProps) {
  const node: RootPromptNode = data.node
  return (
    <CardFrame kindLabel="Root prompt" state={node.state} nodeId={node.id} showTargetHandle={false}>
      <CardBody text={node.params.text} />
      <MetaRow label="target" value={node.params.targetRegistryName} />
    </CardFrame>
  )
}

// ============================================================================
// ImportMessageCard
// ============================================================================

type ImportMessageProps = NodeProps<Extract<TreeFlowNode, { type: 'import_message' }>>

export function ImportMessageCard({ data }: ImportMessageProps) {
  const node: ImportMessageNode = data.node
  return (
    <CardFrame kindLabel="Imported message" state={node.state} nodeId={node.id} showTargetHandle={false}>
      <MetaRow label="source" value={node.params.sourceConversationId} />
      <MetaRow label="cutoff" value={String(node.params.cutoffIndex)} />
    </CardFrame>
  )
}

// ============================================================================
// UserTurnCard
// ============================================================================

type UserTurnProps = NodeProps<Extract<TreeFlowNode, { type: 'user_turn' }>>

export function UserTurnCard({ data }: UserTurnProps) {
  const node: UserTurnNode = data.node
  const converters = node.params.converterPipeline ?? []
  return (
    <CardFrame kindLabel="User turn" state={node.state} nodeId={node.id}>
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

export function SendCard({ data }: SendProps) {
  const node: SendNode = data.node
  return (
    <CardFrame kindLabel="Send" state={node.state} nodeId={node.id}>
      {node.params.targetRegistryName !== undefined && (
        <MetaRow label="target" value={node.params.targetRegistryName} />
      )}
      {node.state === 'failed' && node.lastError !== null && (
        <div
          style={{
            marginTop: 6,
            padding: '4px 6px',
            background: '#3a1e1e',
            color: '#e08080',
            borderRadius: 3,
            fontSize: 11,
          }}
        >
          {node.lastError.message}
        </div>
      )}
    </CardFrame>
  )
}

// ============================================================================
// FanCard
// ============================================================================

type FanProps = NodeProps<Extract<TreeFlowNode, { type: 'fan' }>>

export function FanCard({ data }: FanProps) {
  const node: FanNode = data.node
  const n = node.params.variants.length
  return (
    <CardFrame kindLabel="Fan" state={node.state} nodeId={node.id}>
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

export function ScoreCard({ data }: ScoreProps) {
  const node: ScoreNode = data.node
  return (
    <CardFrame kindLabel="Score" state={node.state} nodeId={node.id}>
      <MetaRow label="scorer" value={node.params.scorerType} />
      <div style={{ marginTop: 4, fontSize: 10, opacity: 0.55, fontStyle: 'italic' }}>
        V1.0: displays scores attached to upstream pieces (configuration is V1.1).
      </div>
    </CardFrame>
  )
}
