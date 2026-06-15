// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import {
  Button,
  Menu,
  MenuItem,
  MenuList,
  MenuPopover,
  MenuTrigger,
  Textarea,
  Tooltip,
} from '@fluentui/react-components'
import {
  AddRegular,
  DismissRegular,
  EditRegular,
  FlashRegular,
} from '@fluentui/react-icons'
import type { NodeProps } from '@xyflow/react'
import { useState } from 'react'

import type { UserTurnNode } from '../../runner/treeTypes'
import { useActionCallbacks } from './actionCallbacksContext'
import { useAvailableConverters } from './availableConvertersContext'
import { useEditorKeyboard } from './useEditorKeyboard'
import type { TreeFlowNode } from './conversationTreeToReactFlow'
import { CardBody, CardFrame, MetaRow } from './cardFrame'
import { useNodeCardStyles } from './nodeCards.styles'

type UserTurnProps = NodeProps<Extract<TreeFlowNode, { type: 'user_turn' }>>

export function UserTurnCard({ data, selected }: UserTurnProps) {
  const node: UserTurnNode = data.node
  const converters = node.params.converterPipeline ?? []
  const callbacks = useActionCallbacks()
  const availableConverters = useAvailableConverters()
  const onEditText = callbacks?.onEditUserTurnText
  const onSetPipeline = callbacks?.onSetUserTurnConverterPipeline
  const onAppendChild = callbacks?.onAppendChild
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
    !isEditing && (onEditText !== undefined || showPalette || onAppendChild !== undefined) ? (
      <>
        {onAppendChild !== undefined && (
          <Tooltip content="Add response" relationship="description">
            <Button
              size="small"
              appearance="subtle"
              icon={<AddRegular />}
              aria-label="Add response"
              onClick={() => onAppendChild(node.id, 'send')}
            />
          </Tooltip>
        )}
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
        <ConverterChipRow
          converters={converters}
          available={availableConverters}
          onRemove={
            onSetPipeline !== undefined
              ? (index) =>
                  onSetPipeline(
                    node.id,
                    converters.filter((_c, i) => i !== index),
                  )
              : undefined
          }
        />
      )}
    </CardFrame>
  )
}

/**
 * Per-converter chip row under the UserTurn body. Each chip shows the
 * converter label (looked up in `available`, falling back to its id;
 * `"inline"` for inline-spec converters). When `onRemove` is provided,
 * each chip carries an X button that fires `onRemove(index)` so the
 * card can dispatch the filtered pipeline back to the host. When
 * `onRemove` is omitted, the chips render read-only — there's nothing
 * to dispatch.
 */
export function ConverterChipRow({
  converters,
  available,
  onRemove,
}: {
  converters: ReadonlyArray<{ converterId?: string; inline?: { type: string; params: Record<string, unknown> } }>
  available: ReadonlyArray<{ id: string; label: string }> | null
  onRemove?: (index: number) => void
}) {
  const styles = useNodeCardStyles()
  return (
    <div className={styles.converterChips}>
      {converters.map((c, i) => {
        const label = converterLabel(c, available)
        return (
          <span
            key={i}
            data-testid={`converter-chip-${i}`}
            className={styles.converterChip}
          >
            <span className={styles.converterChipLabel}>{label}</span>
            {onRemove !== undefined && (
              <Button
                size="small"
                appearance="subtle"
                icon={<DismissRegular />}
                aria-label={`Remove ${label}`}
                onClick={() => onRemove(i)}
              />
            )}
          </span>
        )
      })}
    </div>
  )
}

function converterLabel(
  c: { converterId?: string; inline?: { type: string; params: Record<string, unknown> } },
  available: ReadonlyArray<{ id: string; label: string }> | null,
): string {
  if (c.converterId === undefined) return 'inline'
  const match = available?.find((a) => a.id === c.converterId)
  return match?.label ?? c.inline?.type ?? c.converterId
}

/**
 * Inline text editor for the V1.0 edit affordances (UserTurn,
 * RootPrompt — PR5h.5+). Esc cancels; Cmd/Ctrl-Enter saves (plain
 * Enter inserts a newline so multi-line prompts work). The host owns
 * the source of truth — onSave fires `(text)` and the host re-renders
 * the card with new `node.params.text`.
 */
export function InlineTextEditor({
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
  const onKeyDown = useEditorKeyboard({
    onSave: () => onSave(draft),
    onCancel,
  })
  return (
    <div className={styles.inlineEditor}>
      <Textarea
        value={draft}
        onChange={(_e, d) => setDraft(d.value)}
        autoFocus
        rows={3}
        aria-label={ariaLabel}
        onKeyDown={onKeyDown}
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
