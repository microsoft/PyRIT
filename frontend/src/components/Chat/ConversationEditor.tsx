import { useCallback, useImperativeHandle, useMemo, useState, type ReactNode, type Ref } from 'react'

import {
  Button, Checkbox, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle,
  Field, Menu, MenuItem, MenuList, MenuPopover, MenuTrigger, MessageBar, MessageBarBody, Select, Text, Textarea, Tooltip,
} from '@fluentui/react-components'
import { AddRegular, AttachRegular, DismissRegular } from '@fluentui/react-icons'
import { useBeforeUnload, useBlocker } from 'react-router'

import { usePieceConverters } from '@/hooks/useChatConverters'
import type { useConversationDraft } from '@/hooks/useConversationDraft'
import type {
  AddMessageResponse, ConversationDraftMessage, ConversationDraftPiece, MessageAttachment, SaveConversationRequest,
} from '@/types'
import {
  NEW_MESSAGE_ROLES, draftAttachment, draftConverterInputs, editorTargetDisabledReason, unansweredToolCallId,
} from '@/utils/conversationDraft'
import { buildAppliedConversions } from '@/utils/conversionResults'
import { basenameFromValue, buildMediaUrl, dataTypeToAttachmentKind, isPathDataType } from '@/utils/media'

import PromptEditor from './PromptEditor'
import ConverterPanel from './ConverterPanel'
import SaveConversationDialog from './SaveConversationDialog'
import { useConversationEditorStyles } from './ConversationEditor.styles'

interface DraftPieceEditorProps {
  piece: ConversationDraftPiece
  disabled: boolean
  onChange: (pieceId: string, update: (piece: ConversationDraftPiece) => ConversationDraftPiece) => void
  onAttachments: (pieceId: string, attachments: MessageAttachment[]) => void
  onConverters: () => void
  header?: ReactNode
  attachmentMenuItems?: ReactNode
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function ToolPieceEditor({ piece, disabled, onChange }: Pick<DraftPieceEditorProps, 'piece' | 'disabled' | 'onChange'>) {
  const styles = useConversationEditorStyles()
  let data: Record<string, unknown> | undefined
  let error = ''
  const tool = ['function_call', 'function_call_output', 'tool_call'].includes(piece.data_type)
  try {
    const parsed: unknown = JSON.parse(piece.converted_value ?? piece.original_value)
    if (isRecord(parsed)) data = parsed
    else error = 'Tool content must be a JSON object.'
  } catch {
    error = 'Tool content must be valid JSON.'
  }
  if (!tool) error = ''
  const update = (value: string): void => {
    onChange(piece.draftId, (current: ConversationDraftPiece) => ({
      ...current, original_value: value, converted_value: undefined, converted_value_data_type: undefined,
      applied_converter_ids: [],
    }))
  }
  const nested = data && isRecord(data.function) ? data.function : undefined
  const fields = piece.data_type === 'function_call'
    ? ['call_id', 'name', 'arguments'] : piece.data_type === 'function_call_output' ? ['call_id', 'output'] : []
  return (
    <>
      {data && fields.map((field: string) => {
        const key = field === 'call_id' && !('call_id' in data) && 'id' in data ? 'id' : field
        const container = nested && (field === 'name' || field === 'arguments') ? nested : data
        const value = container[key]
        return (
          <Field key={field} label={field}>
            <Textarea
              disabled={disabled}
              value={typeof value === 'string' ? value : JSON.stringify(value) ?? ''}
              onChange={(_event, changed) => update(JSON.stringify(container === nested
                ? { ...data, function: { ...nested, [key]: changed.value } }
                : { ...data, [key]: changed.value }))}
            />
          </Field>
        )
      })}
      <Field label={`Raw ${piece.data_type} content`} validationMessage={error} validationState={error ? 'error' : 'none'}>
        <Textarea
          className={styles.textarea} disabled={disabled} resize="vertical"
          value={piece.converted_value ?? piece.original_value}
          onChange={(_event, changed) => update(changed.value)}
        />
      </Field>
      <Text>Saving does not execute tools. Converters do not change structured tool content.</Text>
    </>
  )
}

function DraftPieceEditor({ piece, disabled, onChange, onAttachments, onConverters, header, attachmentMenuItems }: DraftPieceEditorProps) {
  const media = isPathDataType(piece.data_type)
  const hasConversion = piece.converted_value !== undefined
    && (piece.converted_value !== piece.original_value || piece.converted_value_data_type !== piece.data_type)
  const changeText = useCallback((value: string): void => {
    onChange(piece.draftId, (current: ConversationDraftPiece) => current.original_value === value || media
      ? current : {
        ...current, original_value: value, converted_value: undefined, converted_value_data_type: undefined,
        applied_converter_ids: [],
      })
  }, [media, onChange, piece.draftId])
  const changeAttachments = useCallback((attachments: MessageAttachment[]): void => {
    onAttachments(piece.draftId, attachments)
  }, [onAttachments, piece.draftId])
  const clearConversion = (): void => onChange(piece.draftId, (current: ConversationDraftPiece) => ({
    ...current, converted_value: undefined, converted_value_data_type: undefined, applied_converter_ids: [],
  }))
  if (piece.data_type !== 'text' && !media) {
    return <ToolPiecePrompt piece={piece} disabled={disabled} onChange={onChange} header={header} attachmentMenuItems={attachmentMenuItems} />
  }
  return (
    <PromptEditor
      header={header}
      attachmentMenuItems={attachmentMenuItems}
      showTextInput={!media}
      text={media ? '' : piece.original_value}
      attachments={media ? [draftAttachment(piece)] : []}
      disabled={disabled}
      onToggleConverterPanel={onConverters}
      isConverterPanelOpen={false}
      onInputChange={changeText}
      onAttachmentsChange={changeAttachments}
      convertedValue={hasConversion && piece.converted_value_data_type === 'text' ? piece.converted_value : undefined}
      convertedFileChip={hasConversion && piece.converted_value && piece.converted_value_data_type && isPathDataType(piece.converted_value_data_type)
        ? {
          name: basenameFromValue(piece.converted_value, 'Converted file'),
          url: buildMediaUrl(piece.converted_value),
          iconKind: dataTypeToAttachmentKind(piece.converted_value_data_type),
        } : undefined}
      onClearConvertedFileChip={clearConversion}
      onClearMediaConversion={clearConversion}
      onConvertedValueChange={(value: string) => onChange(piece.draftId, (current: ConversationDraftPiece) => ({
        ...current, converted_value: value,
      }))}
    />
  )
}

function ToolPiecePrompt({ piece, disabled, onChange, header, attachmentMenuItems }: Pick<DraftPieceEditorProps, 'piece' | 'disabled' | 'onChange' | 'header' | 'attachmentMenuItems'>) {
  const styles = useConversationEditorStyles()
  return <div className={styles.toolPrompt}>
    <div className={styles.row}>{header}</div>
    <ToolPieceEditor piece={piece} disabled={disabled} onChange={onChange} />
    <Menu>
      <MenuTrigger disableButtonEnhancement>
        <Button appearance="subtle" size="small" icon={<AttachRegular />} className={styles.button}
          aria-label="Add content" disabled={disabled} />
      </MenuTrigger>
      <MenuPopover><MenuList>{attachmentMenuItems}</MenuList></MenuPopover>
    </Menu>
  </div>
}

interface ConversationEditorProps {
  controller: ReturnType<typeof useConversationDraft>
  ref?: Ref<ConversationEditorHandle>
  sameAttackDisabledReason?: string
  onSaved: (response: AddMessageResponse) => void
}

export interface ConversationEditorHandle {
  convertConversation: () => void
  saveToNewAttack: () => void
}

export default function ConversationEditor({
  controller, ref, sameAttackDisabledReason, onSaved,
}: ConversationEditorProps) {
  const styles = useConversationEditorStyles()
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [convertersOpen, setConvertersOpen] = useState(false)
  const [saveDialogOpen, setSaveDialogOpen] = useState(false)
  const [saveDestination, setSaveDestination] = useState<SaveConversationRequest['destination']>()
  const [discardOpen, setDiscardOpen] = useState(false)
  const { draft, saving, error, dirty, validationError, targetError, changePiece, changeAttachments, insert, addPiece } = controller
  const messages = useMemo(() => draft?.messages ?? [], [draft?.messages])
  const target = draft?.target
  const blocker = useBlocker(controller.shouldBlock)
  useBeforeUnload(useCallback((event: BeforeUnloadEvent): void => {
    if (controller.shouldBlock()) {
      event.preventDefault()
      event.returnValue = ''
    }
  }, [controller]))
  const inputs = useMemo(() => draftConverterInputs(messages, selected), [messages, selected])
  const converters = usePieceConverters(inputs, [...selected].sort().join(','))
  const openConverters = (messageId?: string): void => {
    setSelected(new Set(messageId ? [messageId] : messages.map((message: ConversationDraftMessage) => message.id)))
    setConvertersOpen(true)
  }
  useImperativeHandle(ref, () => ({
    convertConversation: () => openConverters(),
    saveToNewAttack: () => {
      setSaveDestination('new_attack')
      setSaveDialogOpen(true)
    },
  }))
  const removeMessage = (messageId: string): void => {
    controller.removeMessage(messageId)
    setSelected((previous: Set<string>) => new Set([...previous].filter((id: string) => id !== messageId)))
  }
  const applyConversions = (): void => {
    controller.applyConversions(buildAppliedConversions(converters.inputs, converters.results))
    setConvertersOpen(false)
  }
  const unsupportedSelected = messages.some((message: ConversationDraftMessage) => selected.has(message.id)
    && message.pieces.some((piece: ConversationDraftPiece) => piece.data_type !== 'text' && !isPathDataType(piece.data_type)))
  return (
    <div className={styles.root} data-testid="conversation-editor">
      {convertersOpen && <div className={styles.converterPane}>
        <ConverterPanel controller={converters} selectedMessageCount={selected.size}
          onClose={() => setConvertersOpen(false)} onApply={applyConversions} />
      </div>}
      <div className={styles.content}>
      <div className={styles.thread}>
        {validationError && <MessageBar intent="error"><MessageBarBody>{validationError}</MessageBarBody></MessageBar>}
        {targetError && <MessageBar intent="warning"><MessageBarBody>
          {targetError} Select a compatible target or clear the target to save a new unbound attack.
        </MessageBarBody></MessageBar>}
        {convertersOpen && <div className={styles.row}>
            <Button appearance="subtle" className={styles.button} onClick={() => setSelected(new Set(messages.map((message: ConversationDraftMessage) => message.id)))}>Select all</Button>
            <Button appearance="subtle" className={styles.button} onClick={() => setSelected(new Set())}>Clear</Button>
        </div>}
        {unsupportedSelected && convertersOpen && <MessageBar intent="warning"><MessageBarBody>
          Structured pieces are not converted. Their content and tool links stay unchanged.
        </MessageBarBody></MessageBar>}
        <div className={styles.insertRow}>
          <Button appearance="transparent" size="small" icon={<AddRegular />} className={styles.insertButton}
            aria-label="Insert message at start" disabled={saving} onClick={() => insert(0)}>Insert message</Button>
        </div>
        {messages.map((message: ConversationDraftMessage, index: number) => (
          <div key={message.id}>
            <section className={styles.card} aria-label={`Message ${index + 1}`}>
              {message.pieces.map((piece: ConversationDraftPiece, pieceIndex: number) => (
                <div key={piece.draftId} className={styles.piece}>
                  <DraftPieceEditor piece={piece} disabled={saving} onChange={changePiece}
                    onAttachments={changeAttachments} onConverters={() => openConverters(message.id)}
                    attachmentMenuItems={<>
                      <MenuItem onClick={() => addPiece(message.id, 'text')}>Add text</MenuItem>
                      <MenuItem disabled={message.role !== 'simulated_assistant'
                        || Boolean(target && editorTargetDisabledReason(target, ['function_call']))}
                        title={message.role !== 'simulated_assistant' ? 'Tool calls need the simulated_assistant role.'
                          : target ? editorTargetDisabledReason(target, ['function_call']) : undefined}
                        onClick={() => addPiece(message.id, 'function_call')}>Add tool call</MenuItem>
                      <MenuItem disabled={message.role !== 'simulated_assistant' || !unansweredToolCallId(messages, message.id)
                        || Boolean(target && editorTargetDisabledReason(target, ['function_call_output']))}
                        title={target && editorTargetDisabledReason(target, ['function_call_output'])
                          || 'Add a linked tool response after this message. A preceding unanswered call ID is required.'}
                        onClick={() => addPiece(message.id, 'function_call_output')}>Add tool response</MenuItem>
                    </>}
                    header={<>
                      {pieceIndex === 0 && <>
                        {convertersOpen && <Checkbox aria-label={`Select message ${index + 1}`} checked={selected.has(message.id)}
                          onChange={(_event, data) => setSelected((previous: Set<string>) => {
                            const next = new Set(previous)
                            if (data.checked) next.add(message.id)
                            else next.delete(message.id)
                            return next
                          })} />}
                        <Select appearance="underline" size="small" className={styles.role}
                          aria-label={`Role for message ${index + 1}`} value={message.role} disabled={saving}
                          onChange={(_event, data) => {
                            const role = NEW_MESSAGE_ROLES.find((candidate: string) => candidate === data.value)
                            if (role) controller.changeMessages((current: ConversationDraftMessage[]) => current.map(
                              (item: ConversationDraftMessage) => item.id === message.id ? { ...item, role } : item,
                            ))
                          }}>
                          {!NEW_MESSAGE_ROLES.includes(message.role) && <option value={message.role}>{message.role}</option>}
                          {NEW_MESSAGE_ROLES.map((role) => <option key={role} value={role}>{role}</option>)}
                        </Select>
                      </>}
                      <span className={styles.headerSpacer} />
                      {pieceIndex === 0 ? <Tooltip content="Delete message" relationship="label">
                        <Button appearance="transparent" size="small" icon={<DismissRegular />} className={styles.button}
                          aria-label={`Delete message ${index + 1}`} disabled={saving} onClick={() => removeMessage(message.id)} />
                      </Tooltip> : <Tooltip content="Remove piece" relationship="label">
                        <Button appearance="transparent" size="small" icon={<DismissRegular />} className={styles.button}
                          aria-label={`Remove piece ${pieceIndex + 1} from message ${index + 1}`} disabled={saving}
                          onClick={() => controller.changeMessages((current: ConversationDraftMessage[]) => current.map(
                            (item: ConversationDraftMessage) => item.id === message.id
                              ? { ...item, pieces: item.pieces.filter((candidate: ConversationDraftPiece) => candidate.draftId !== piece.draftId) } : item,
                          ))} />
                      </Tooltip>}
                    </>}
                  />
                </div>
              ))}
            </section>
            <div className={styles.insertRow}>
              <Button appearance="transparent" size="small" icon={<AddRegular />} className={styles.insertButton}
                aria-label={`Insert message after message ${index + 1}`} disabled={saving} onClick={() => insert(index + 1)}>Insert message</Button>
            </div>
          </div>
        ))}
      </div>
      <div className={styles.footer}>
        <Button className={styles.button} disabled={saving} onClick={() => { if (dirty) setDiscardOpen(true); else controller.discard() }}>Cancel</Button>
        <Button className={styles.button} appearance="primary" disabled={saving || Boolean(validationError)} onClick={() => setSaveDialogOpen(true)}>Save conversation</Button>
      </div>
      </div>
      {saveDialogOpen && <SaveConversationDialog sameAttackDisabledReason={sameAttackDisabledReason}
        validationError={validationError ?? targetError}
        initialDestination={saveDestination}
        objectiveChanged={draft?.objective.trim() !== draft?.initialObjective} saving={saving} error={error}
        onClose={() => setSaveDialogOpen(false)} onSave={(destination) => { void controller.save(destination, onSaved) }} />}
      <Dialog open={discardOpen || blocker.state === 'blocked'} onOpenChange={(_event, data) => {
        if (!data.open && !saving) { setDiscardOpen(false); if (blocker.state === 'blocked') blocker.reset() }
      }}>
        <DialogSurface><DialogBody>
          <DialogTitle>Discard conversation draft?</DialogTitle>
          <DialogContent>Your unsaved changes will be lost.</DialogContent>
          <DialogActions>
            <Button className={styles.button} onClick={() => { setDiscardOpen(false); if (blocker.state === 'blocked') blocker.reset() }}>Keep editing</Button>
            <Button className={styles.button} disabled={saving} onClick={() => {
              controller.discard()
              if (blocker.state === 'blocked') blocker.proceed()
            }}>Discard draft</Button>
          </DialogActions>
        </DialogBody></DialogSurface>
      </Dialog>
    </div>
  )
}
