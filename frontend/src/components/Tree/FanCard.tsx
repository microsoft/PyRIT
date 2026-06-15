// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import {
  Button,
  Menu,
  MenuItem,
  MenuList,
  MenuPopover,
  MenuTrigger,
  Tooltip,
} from '@fluentui/react-components'
import {
  ArrowMaximizeRegular,
  ArrowMinimizeRegular,
  CheckmarkCircleFilled,
  CheckmarkCircleRegular,
} from '@fluentui/react-icons'
import type { NodeProps } from '@xyflow/react'

import type {
  ConversationTreeNodeId,
  FanNode,
} from '../../runner/treeTypes'
import { useActionCallbacks } from './actionCallbacksContext'
import { useStackCollapse } from './stackCollapseContext'
import type { StackAggregate, StackMember } from './fanStack'
import type { TreeFlowNode } from './conversationTreeToReactFlow'
import { CardFrame, MetaRow } from './cardFrame'
import { useNodeCardStyles } from './nodeCards.styles'

type FanProps = NodeProps<Extract<TreeFlowNode, { type: 'fan' }>>

export function FanCard({ data, selected }: FanProps) {
  const node: FanNode = data.node
  const n = node.params.variants.length
  const stack = data.stackedSummary
  const collapseCtx = useStackCollapse()
  const callbacks = useActionCallbacks()
  const onPickFanChild = callbacks?.onPickFanChild
  const onPruneFanToPickedPath = callbacks?.onPruneFanToPickedPath
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
      {onPruneFanToPickedPath !== undefined && (
        <PruneFanButton
          fanNodeId={node.id}
          variants={node.params.variants.length}
          promotedSlot={node.params.promotedChildSlotIndex}
          onPruneFanToPickedPath={onPruneFanToPickedPath}
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

function PruneFanButton({
  fanNodeId,
  variants,
  promotedSlot,
  onPruneFanToPickedPath,
}: {
  fanNodeId: ConversationTreeNodeId
  variants: number
  promotedSlot: number | null
  onPruneFanToPickedPath: (id: ConversationTreeNodeId, slotIndex: number) => void
}) {
  if (promotedSlot !== null) {
    return (
      <div data-tree-fan-prune>
        <Tooltip content={`Prune to picked slot ${promotedSlot}`} relationship="description">
          <Button
            size="small"
            appearance="subtle"
            aria-label={`Prune to picked slot ${promotedSlot}`}
            onClick={() => onPruneFanToPickedPath(fanNodeId, promotedSlot)}
          >
            Prune
          </Button>
        </Tooltip>
      </div>
    )
  }

  return (
    <div data-tree-fan-prune>
      <Menu positioning="below">
        <MenuTrigger disableButtonEnhancement>
          <Tooltip content="Prune to a slot" relationship="description">
            <Button size="small" appearance="subtle" aria-label="Prune to a slot">
              Prune
            </Button>
          </Tooltip>
        </MenuTrigger>
        <MenuPopover>
          <MenuList>
            {Array.from({ length: variants }, (_unused, slot) => (
              <MenuItem key={slot} onClick={() => onPruneFanToPickedPath(fanNodeId, slot)}>
                Keep slot {slot}
              </MenuItem>
            ))}
          </MenuList>
        </MenuPopover>
      </Menu>
    </div>
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
