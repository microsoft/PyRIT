// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import {
  Button,
  Input,
  Textarea,
  Tooltip,
} from '@fluentui/react-components'
import { EditRegular } from '@fluentui/react-icons'
import type { NodeProps } from '@xyflow/react'
import { useState } from 'react'

import type { RootPromptNode } from '../../runner/treeTypes'
import { useActionCallbacks } from './actionCallbacksContext'
import { useEditorKeyboard } from './useEditorKeyboard'
import type { TreeFlowNode } from './conversationTreeToReactFlow'
import { CardBody, CardFrame, MetaRow } from './cardFrame'
import { useNodeCardStyles } from './nodeCards.styles'

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
      canDelete={false}
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
  const onKeyDown = useEditorKeyboard({ onSave: commit, onCancel })
  return (
    <div className={styles.inlineEditor}>
      <Textarea
        value={text}
        onChange={(_e, d) => setText(d.value)}
        autoFocus
        rows={3}
        aria-label="Prompt text"
        onKeyDown={onKeyDown}
      />
      <Textarea
        value={systemPrompt}
        onChange={(_e, d) => setSystemPrompt(d.value)}
        rows={2}
        placeholder="System prompt (optional)"
        aria-label="System prompt"
        onKeyDown={onKeyDown}
      />
      <Input
        value={target}
        onChange={(_e, d) => setTarget(d.value)}
        placeholder="Target registry name"
        aria-label="Target registry name"
        onKeyDown={onKeyDown}
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
