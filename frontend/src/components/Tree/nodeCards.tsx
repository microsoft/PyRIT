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

import {
  Button,
  Input,
  Menu,
  MenuItem,
  MenuList,
  MenuPopover,
  MenuTrigger,
  Textarea,
  Tooltip,
  mergeClasses,
} from '@fluentui/react-components'
import {
  ArrowMaximizeRegular,
  ArrowMinimizeRegular,
  CheckmarkCircleFilled,
  CheckmarkCircleRegular,
  EditRegular,
  FlashRegular,
} from '@fluentui/react-icons'
import { Handle, Position } from '@xyflow/react'
import type { NodeProps } from '@xyflow/react'
import { useState } from 'react'

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
import { useAvailableConverters } from './availableConvertersContext'
import type { FanChildInfo, TreeFlowNode } from './conversationTreeToReactFlow'
import type { StackAggregate, StackMember } from './fanStack'
import { STATE_BADGE_TOKENS, useNodeCardStyles } from './nodeCards.styles'
import { useStackCollapse } from './stackCollapseContext'

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
  fanChildInfo,
  kindActions,
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
        <ActionRail
          nodeId={nodeId}
          callbacks={callbacks}
          branchLabel={branchLabel}
          fanChildInfo={fanChildInfo}
          kindActions={kindActions}
        />
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

function MetaRow({
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

// ============================================================================
// RootPromptCard
// ============================================================================

type RootPromptProps = NodeProps<Extract<TreeFlowNode, { type: 'root_prompt' }>>

export function RootPromptCard({ data, selected }: RootPromptProps) {
  const node: RootPromptNode = data.node
  const callbacks = useActionCallbacks()
  const onEditParams = callbacks?.onEditRootPromptParams
  const [isEditing, setIsEditing] = useState(false)
  const kindActions =
    onEditParams !== undefined && !isEditing ? (
      <Tooltip content="Edit root prompt" relationship="description">
        <Button
          size="small"
          appearance="subtle"
          icon={<EditRegular />}
          aria-label="Edit root prompt"
          onClick={() => setIsEditing(true)}
        />
      </Tooltip>
    ) : undefined
  return (
    <CardFrame
      kindLabel="Root prompt"
      state={node.state}
      nodeId={node.id}
      selected={selected}
      branchLabel="Clone tree"
      fanChildInfo={data.fanChildInfo}
      kindActions={kindActions}
      showTargetHandle={false}
    >
      {isEditing && onEditParams !== undefined ? (
        <InlineRootPromptEditor
          initialText={node.params.text}
          initialSystemPrompt={node.params.systemPrompt ?? ''}
          initialTarget={node.params.targetRegistryName}
          onSave={(patch) => {
            onEditParams(node.id, patch)
            setIsEditing(false)
          }}
          onCancel={() => setIsEditing(false)}
        />
      ) : (
        <>
          <CardBody text={node.params.text} />
          <MetaRow label="target" value={node.params.targetRegistryName} />
        </>
      )}
    </CardFrame>
  )
}

/**
 * Three-field editor for RootPrompt (text + systemPrompt + target).
 * Save fires the full patch; partial-field-only edits are a V1.0.1
 * concern. Esc cancels; Cmd/Ctrl-Enter on the prompt field saves.
 */
function InlineRootPromptEditor({
  initialText,
  initialSystemPrompt,
  initialTarget,
  onSave,
  onCancel,
}: {
  initialText: string
  initialSystemPrompt: string
  initialTarget: string
  onSave: (patch: {
    text: string
    systemPrompt: string
    targetRegistryName: string
  }) => void
  onCancel: () => void
}) {
  const styles = useNodeCardStyles()
  const [text, setText] = useState(initialText)
  const [systemPrompt, setSystemPrompt] = useState(initialSystemPrompt)
  const [target, setTarget] = useState(initialTarget)
  const commit = () =>
    onSave({ text, systemPrompt, targetRegistryName: target })
  return (
    <div className={styles.inlineEditor}>
      <Textarea
        value={text}
        onChange={(_e, d) => setText(d.value)}
        autoFocus
        rows={3}
        aria-label="Prompt text"
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            e.preventDefault()
            onCancel()
          } else if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
            e.preventDefault()
            commit()
          }
        }}
      />
      <Textarea
        value={systemPrompt}
        onChange={(_e, d) => setSystemPrompt(d.value)}
        rows={2}
        placeholder="System prompt (optional)"
        aria-label="System prompt"
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            e.preventDefault()
            onCancel()
          }
        }}
      />
      <Input
        value={target}
        onChange={(_e, d) => setTarget(d.value)}
        placeholder="Target registry name"
        aria-label="Target registry name"
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            e.preventDefault()
            onCancel()
          }
        }}
      />
      <div className={styles.inlineEditorActions}>
        <Button size="small" appearance="primary" onClick={commit}>
          Save
        </Button>
        <Button size="small" appearance="subtle" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
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
      fanChildInfo={data.fanChildInfo}
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
  const callbacks = useActionCallbacks()
  const availableConverters = useAvailableConverters()
  const onEditText = callbacks?.onEditUserTurnText
  const onSetPipeline = callbacks?.onSetUserTurnConverterPipeline
  const [isEditing, setIsEditing] = useState(false)
  const showPalette =
    onSetPipeline !== undefined &&
    availableConverters !== null &&
    availableConverters.length > 0 &&
    !isEditing
  const onPickConverter = (id: string) => {
    if (onSetPipeline === undefined) return
    onSetPipeline(node.id, [...converters, { converterId: id }])
  }
  const kindActions =
    !isEditing && (onEditText !== undefined || showPalette) ? (
      <>
        {onEditText !== undefined && (
          <Tooltip content="Edit text inline" relationship="description">
            <Button
              size="small"
              appearance="subtle"
              icon={<EditRegular />}
              aria-label="Edit text inline"
              onClick={() => setIsEditing(true)}
            />
          </Tooltip>
        )}
        {showPalette && (
          <Menu>
            <MenuTrigger disableButtonEnhancement>
              <Tooltip content="Open converter palette" relationship="description">
                <Button
                  size="small"
                  appearance="subtle"
                  icon={<FlashRegular />}
                  aria-label="Open converter palette"
                />
              </Tooltip>
            </MenuTrigger>
            <MenuPopover>
              <MenuList>
                {availableConverters!.map((c) => (
                  <MenuItem key={c.id} onClick={() => onPickConverter(c.id)}>
                    {c.label}
                  </MenuItem>
                ))}
              </MenuList>
            </MenuPopover>
          </Menu>
        )}
      </>
    ) : undefined
  return (
    <CardFrame
      kindLabel="User turn"
      state={node.state}
      nodeId={node.id}
      selected={selected}
      branchLabel="Branch from here"
      fanChildInfo={data.fanChildInfo}
      kindActions={kindActions}
    >
      {isEditing && onEditText !== undefined ? (
        <InlineTextEditor
          initial={node.params.text}
          ariaLabel="Edit user turn text"
          onSave={(text) => {
            onEditText(node.id, text)
            setIsEditing(false)
          }}
          onCancel={() => setIsEditing(false)}
        />
      ) : (
        <CardBody text={node.params.text} />
      )}
      <MetaRow label="role" value={node.params.role} />
      {converters.length > 0 && (
        <MetaRow label="" value={`${converters.length} converter${converters.length === 1 ? '' : 's'}`} />
      )}
    </CardFrame>
  )
}

/**
 * Inline text editor for the V1.0 edit affordances (UserTurn,
 * RootPrompt — PR5h.5+). Esc cancels; Cmd/Ctrl-Enter saves (plain
 * Enter inserts a newline so multi-line prompts work). The host owns
 * the source of truth — onSave fires `(text)` and the host re-renders
 * the card with new `node.params.text`.
 */
function InlineTextEditor({
  initial,
  ariaLabel,
  onSave,
  onCancel,
}: {
  initial: string
  ariaLabel: string
  onSave: (text: string) => void
  onCancel: () => void
}) {
  const styles = useNodeCardStyles()
  const [draft, setDraft] = useState(initial)
  return (
    <div className={styles.inlineEditor}>
      <Textarea
        value={draft}
        onChange={(_e, d) => setDraft(d.value)}
        autoFocus
        rows={3}
        aria-label={ariaLabel}
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            e.preventDefault()
            onCancel()
          } else if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
            e.preventDefault()
            onSave(draft)
          }
        }}
      />
      <div className={styles.inlineEditorActions}>
        <Button size="small" appearance="primary" onClick={() => onSave(draft)}>
          Save
        </Button>
        <Button size="small" appearance="subtle" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
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
      fanChildInfo={data.fanChildInfo}
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
  const stack = data.stackedSummary
  const collapseCtx = useStackCollapse()
  const callbacks = useActionCallbacks()
  const onPickFanChild = callbacks?.onPickFanChild
  return (
    <CardFrame
      kindLabel="Fan"
      state={node.state}
      nodeId={node.id}
      selected={selected}
      branchLabel="Branch from here"
      fanChildInfo={data.fanChildInfo}
    >
      <MetaRow label="axis" value={node.params.axis} />
      <MetaRow label="" value={`${n} variant${n === 1 ? '' : 's'}`} />
      {node.params.promotedChildSlotIndex !== null && (
        <MetaRow
          label="pick"
          value={`slot ${node.params.promotedChildSlotIndex}`}
          title="Visual focus only. Future releases will scope Refresh and Stack-edit to the picked attempt."
        />
      )}
      {stack !== undefined && <StackSummaryBody summary={stack} />}
      {stack !== undefined && onPickFanChild !== undefined && (
        <StackPickButton
          fanNodeId={node.id}
          members={stack.members}
          promotedSlot={node.params.promotedChildSlotIndex}
          onPickFanChild={onPickFanChild}
        />
      )}
      {collapseCtx !== null && (
        <StackToggleButton
          collapsed={stack !== undefined}
          onToggle={() => collapseCtx.toggleStack(node.id)}
        />
      )}
    </CardFrame>
  )
}

/**
 * Inline body shown inside the FanCard when the fan is in the collapsed
 * (stacked) state. Renders the multiplicity ("Send ×10") and aggregate
 * status ("9 ✓, 1 ⚠") so operators see at a glance how the stacked
 * children are doing.
 */
function StackSummaryBody({ summary }: { summary: StackAggregate }) {
  const styles = useNodeCardStyles()
  const successful = summary.byState.clean
  const running = summary.byState.running
  const failed = summary.byState.failed + summary.byState.cancelled
  const pending =
    summary.byState.draft +
    summary.byState.edited +
    summary.byState.stale
  const parts: string[] = []
  if (successful > 0) parts.push(`${successful} ✓`)
  if (running > 0) parts.push(`${running} ●`)
  if (failed > 0) parts.push(`${failed} ⚠`)
  if (pending > 0) parts.push(`${pending} ⧖`)
  const statusLine = parts.length > 0 ? parts.join(', ') : '—'
  const kindLabel = summary.childKind ?? 'item'
  return (
    <div
      data-tree-stack-summary
      className={styles.stackSummary}
      title={`${summary.total} ${kindLabel}${summary.total === 1 ? '' : 's'}: ${statusLine}`}
    >
      <span className={styles.stackKindLabel}>
        {kindLabel} ×{summary.total}
      </span>
      <span className={styles.stackStatusLine}>{statusLine}</span>
    </div>
  )
}

function StackToggleButton({
  collapsed,
  onToggle,
}: {
  collapsed: boolean
  onToggle: () => void
}) {
  const label = collapsed ? 'Expand stack' : 'Collapse to stack'
  return (
    <div data-tree-stack-toggle>
      <Tooltip content={label} relationship="description">
        <Button
          size="small"
          appearance="subtle"
          icon={collapsed ? <ArrowMaximizeRegular /> : <ArrowMinimizeRegular />}
          aria-label={label}
          onClick={onToggle}
        />
      </Tooltip>
    </div>
  )
}

/**
 * Collapsed-stack Pick popover. The operator-friendly alternative to
 * "expand the stack first, then click each child's Pick icon" (which
 * was the four-clicks-per-decision flow the PR5f reviewer flagged as
 * unusable for the dominant workflow). Lists each member as a Fluent
 * MenuItem with a per-state glyph + slot number; the currently-promoted
 * member shows "(picked)" and clicking it unpicks (null).
 */
function StackPickButton({
  fanNodeId,
  members,
  promotedSlot,
  onPickFanChild,
}: {
  fanNodeId: ConversationTreeNodeId
  members: ReadonlyArray<StackMember>
  promotedSlot: number | null
  onPickFanChild: (id: ConversationTreeNodeId, slotIndex: number | null) => void
}) {
  return (
    <div data-tree-stack-pick>
      <Menu positioning="below">
        <MenuTrigger disableButtonEnhancement>
          <Tooltip content="Pick best attempt from stack" relationship="description">
            <Button
              size="small"
              appearance="subtle"
              icon={<CheckmarkCircleRegular />}
              aria-label="Pick best attempt from stack"
            >
              Pick…
            </Button>
          </Tooltip>
        </MenuTrigger>
        <MenuPopover>
          <MenuList>
            {members.map((m) => {
              const isPromoted = promotedSlot !== null && promotedSlot === m.slotIndex
              const next = isPromoted ? null : m.slotIndex
              const label = `slot ${m.slotIndex} (${m.state})${isPromoted ? ' ✓ (picked)' : ''}`
              return (
                <MenuItem
                  key={m.id}
                  icon={isPromoted ? <CheckmarkCircleFilled /> : <CheckmarkCircleRegular />}
                  onClick={() => onPickFanChild(fanNodeId, next)}
                >
                  {label}
                </MenuItem>
              )
            })}
          </MenuList>
        </MenuPopover>
      </Menu>
    </div>
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
      fanChildInfo={data.fanChildInfo}
    >
      <MetaRow label="scorer" value={node.params.scorerType} />
      <div className={styles.mutedFooter}>Read-only display</div>
    </CardFrame>
  )
}
