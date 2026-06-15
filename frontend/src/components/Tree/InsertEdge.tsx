// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Per-edge insert chip + popover. Custom react-flow edge component that
 * extends the smoothstep path with a `+` button at the midpoint;
 * clicking the chip opens a kind-aware Fluent Menu of insert options.
 *
 * Chip visibility is gated on the host having supplied an
 * `onEdgeInsert` callback (via ActionCallbacksContext) AND the parent
 * being a kind that admits any legal insert (Score and Fan parents
 * suppress the chip — see PARENTS_WITHOUT_INSERT below).
 */

import { useMemo, useState } from 'react'
import {
  BaseEdge,
  EdgeLabelRenderer,
  getSmoothStepPath,
  useStore,
} from '@xyflow/react'
import type { EdgeProps } from '@xyflow/react'
import {
  Button,
  Menu,
  MenuItem,
  MenuList,
  MenuPopover,
  MenuTrigger,
  Tooltip,
} from '@fluentui/react-components'
import { AddRegular } from '@fluentui/react-icons'

import { useActionCallbacks } from './actionCallbacksContext'
import type { EdgeInsertKind } from './actionRail'
import type { TreeFlowEdge } from './conversationTreeToReactFlow'
import { useInsertEdgeStyles } from './insertEdge.styles'
import type { ConversationTreeNodeId, ConversationTreeNodeKind } from '../../runner/treeTypes'

// Parents whose edges do NOT show the chip. Score is terminal (no
// post-Score insert in V1.0); Fan children are managed via the FanCard's
// own `+` (add variant) button, not via the edge below the Fan.
const PARENTS_WITHOUT_INSERT: ReadonlySet<ConversationTreeNodeKind> = new Set([
  'score',
  'fan',
])

/**
 * Discriminated union: disabled items deliberately have no `kind`
 * field. A V1.1-disabled item that would otherwise be tempted to
 * mint a placeholder `kind` (then silently dispatch the wrong axis
 * once the flag flips) cannot — the type system rejects it. Enabling
 * the item requires also picking a real `kind` at the same change.
 */
export type InsertMenuOption =
  | {
      readonly disabled: false
      readonly kind: EdgeInsertKind
      readonly label: string
    }
  | {
      readonly disabled: true
      readonly label: string
      readonly disabledReason: string
    }

interface InsertMenu {
  basic: InsertMenuOption[]
  fanAxes: ReadonlyArray<InsertMenuOption> // submenu items
}

/**
 * Per-parent menu. The legal next-node types depend on the upstream
 * node's kind — surfacing only the legal options is cheaper than
 * showing all + erroring on commit.
 */
function menuForParent(parentKind: ConversationTreeNodeKind): InsertMenu | null {
  switch (parentKind) {
    case 'root_prompt':
      return {
        basic: [
          { disabled: false, kind: 'follow_up_user_turn', label: 'Follow-up user message' },
          { disabled: false, kind: 'inject_assistant_text', label: 'Inject assistant text' },
          { disabled: false, kind: 'send', label: 'Add response' },
        ],
        fanAxes: V1_0_FAN_AXES,
      }
    case 'import_message':
      return {
        basic: [
          { disabled: false, kind: 'follow_up_user_turn', label: 'Follow-up user message' },
          { disabled: false, kind: 'inject_assistant_text', label: 'Inject assistant text' },
          { disabled: false, kind: 'send', label: 'Add response' },
        ],
        fanAxes: V1_0_FAN_AXES,
      }
    case 'user_turn':
      return {
        basic: [
          { disabled: false, kind: 'send', label: 'Add response' },
          { disabled: false, kind: 'append_converter', label: 'Append converter' },
        ],
        fanAxes: [
          { disabled: false, kind: 'fan_converter', label: 'Fan out: converter' },
        ],
      }
    case 'converter':
      return {
        basic: [
          { disabled: false, kind: 'send', label: 'Add response' },
        ],
        fanAxes: [],
      }
    case 'send':
      return {
        basic: [
          { disabled: false, kind: 'follow_up_user_turn', label: 'Follow-up user message' },
          { disabled: false, kind: 'inject_assistant_text', label: 'Inject assistant text' },
          { disabled: false, kind: 'score', label: 'Score' },
        ],
        fanAxes: V1_0_FAN_AXES,
      }
    case 'score':
    case 'fan':
      return null
  }
}

const V1_0_FAN_AXES: ReadonlyArray<InsertMenuOption> = [
  { disabled: false, kind: 'fan_attempt', label: 'Fan out: attempt' },
  { disabled: false, kind: 'fan_converter', label: 'Fan out: converter' },
]

export function InsertEdge({
  id,
  source,
  target,
  sourceX,
  sourceY,
  sourcePosition,
  targetX,
  targetY,
  targetPosition,
  data,
  style,
  markerEnd,
}: EdgeProps<TreeFlowEdge>) {
  const callbacks = useActionCallbacks()
  const styles = useInsertEdgeStyles()
  const [open, setOpen] = useState(false)
  // EdgeLabelRenderer portals into the `.react-flow__edgelabel-renderer`
  // div, which exists only inside the full <ReactFlow> render tree (NOT
  // inside a bare ReactFlowProvider). When testing the edge directly (no
  // <ReactFlow> mounted), the portal target is absent and the chip falls
  // back to rendering inline. Production always has the portal target;
  // the visual is the same either way.
  const hasPortalTarget = useStore((s) => Boolean(s.domNode?.querySelector('.react-flow__edgelabel-renderer')))

  const parentKind = data?.parentKind
  const menu = useMemo(
    () => (parentKind !== undefined ? menuForParent(parentKind) : null),
    [parentKind],
  )

  const [edgePath, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  })

  const showChip =
    callbacks?.onEdgeInsert !== undefined &&
    parentKind !== undefined &&
    !PARENTS_WITHOUT_INSERT.has(parentKind) &&
    menu !== null

  if (!showChip) {
    return <BaseEdge id={id} path={edgePath} style={style} markerEnd={markerEnd} />
  }

  const onEdgeInsert = callbacks!.onEdgeInsert!
  const handleSelect = (kind: EdgeInsertKind) => {
    // react-flow's EdgeProps types source/target as plain strings; brand
    // them back to ConversationTreeNodeId at the callback boundary so
    // hosts get the same type the runner uses everywhere else.
    onEdgeInsert(
      source as ConversationTreeNodeId,
      target as ConversationTreeNodeId,
      kind,
    )
    setOpen(false)
  }

  const chip = (
    <div
      data-tree-edge-insert
      data-source-id={source}
      data-target-id={target}
      data-source-kind={parentKind}
      className={styles.chipWrapper}
      style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
    >
      <Menu open={open} onOpenChange={(_e, d) => setOpen(d.open)} positioning="below">
        <MenuTrigger disableButtonEnhancement>
          <Tooltip content={`Insert after ${parentLabel(parentKind!)}`} relationship="description">
            <Button
              size="small"
              appearance="primary"
              icon={<AddRegular />}
              aria-label={`Insert after ${parentLabel(parentKind!)}`}
              className={styles.chipButton}
            />
          </Tooltip>
        </MenuTrigger>
        <MenuPopover>
          <MenuList>
            {menu!.basic.map((opt) => (
              <MenuItem
                key={opt.label}
                disabled={opt.disabled}
                title={opt.disabled ? opt.disabledReason : undefined}
                onClick={() => {
                  if (opt.disabled === false) handleSelect(opt.kind)
                }}
              >
                {opt.label}
              </MenuItem>
            ))}
            {menu!.fanAxes.map((opt) => (
              <MenuItem
                key={opt.label}
                disabled={opt.disabled}
                title={opt.disabled ? opt.disabledReason : undefined}
                onClick={() => {
                  if (opt.disabled === false) handleSelect(opt.kind)
                }}
              >
                {opt.label}
              </MenuItem>
            ))}
          </MenuList>
        </MenuPopover>
      </Menu>
    </div>
  )

  return (
    <>
      <BaseEdge id={id} path={edgePath} style={style} markerEnd={markerEnd} />
      {hasPortalTarget ? <EdgeLabelRenderer>{chip}</EdgeLabelRenderer> : chip}
    </>
  )
}

function parentLabel(kind: ConversationTreeNodeKind): string {
  switch (kind) {
    case 'root_prompt':
      return 'root prompt'
    case 'import_message':
      return 'imported message'
    case 'user_turn':
      return 'user turn'
    case 'converter':
      return 'converter'
    case 'send':
      return 'response'
    case 'fan':
      return 'fan'
    case 'score':
      return 'score'
  }
}
