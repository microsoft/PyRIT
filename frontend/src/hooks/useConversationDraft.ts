import { useCallback, useEffect, useRef, useState } from 'react'

import { toApiError } from '@/services/errors'
import type {
  AddMessageResponse, ConversationDraftMessage, ConversationDraftPiece, ConversationSaveInput,
  MessageAttachment, PieceConversion, SaveConversationRequest, TargetInstance,
} from '@/types'
import { generateClientId } from '@/utils/clientId'
import {
  attachmentDraft, draftToolTypes, editorTargetDisabledReason, newDraftMessage, newDraftPiece,
  unansweredToolCallId, validateDraft,
} from '@/utils/conversationDraft'
import { isPathDataType } from '@/utils/media'
import { targetReference } from '@/utils/targetIdentity'

import { useConversationSave } from './useConversationSave'

function targetKey(target: TargetInstance | null): string | undefined {
  return target ? JSON.stringify(targetReference(target)) : undefined
}

interface DraftState extends ConversationSaveInput {
  id: string
  baselineMessages: ConversationDraftMessage[]
  baselineTarget: string | undefined
}

export function useConversationDraft() {
  const [draft, setDraft] = useState<DraftState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const activeId = useRef<string | null>(null)
  const saved = useRef(false)
  const urls = useRef(new Set<string>())
  const workflow = useConversationSave()
  const dirty = draft !== null && (
    draft.messages !== draft.baselineMessages || draft.objective !== draft.initialObjective
    || targetKey(draft.target) !== draft.baselineTarget
  )
  const validationError = draft ? validateDraft(draft.messages) : null
  const targetError = draft?.target
    ? editorTargetDisabledReason(draft.target, draftToolTypes(draft.messages)) : undefined

  const releaseUrls = useCallback((): void => {
    for (const url of urls.current) URL.revokeObjectURL(url)
    urls.current.clear()
  }, [])
  useEffect(() => () => { activeId.current = null; releaseUrls() }, [releaseUrls])

  const begin = useCallback((input: ConversationSaveInput): void => {
    releaseUrls()
    const id = generateClientId()
    activeId.current = id
    saved.current = false
    setError(null)
    setDraft({ ...input, id, baselineMessages: input.messages, baselineTarget: targetKey(input.target) })
  }, [releaseUrls])
  const discard = useCallback((): void => {
    activeId.current = null
    saved.current = true
    releaseUrls()
    setDraft(null)
    setError(null)
  }, [releaseUrls])
  const changeMessages = useCallback((
    update: (messages: ConversationDraftMessage[]) => ConversationDraftMessage[],
  ): void => {
    setDraft((current: DraftState | null) => current ? { ...current, messages: update(current.messages) } : current)
  }, [])
  const changePiece = useCallback((
    pieceId: string, update: (piece: ConversationDraftPiece) => ConversationDraftPiece,
  ): void => {
    changeMessages((messages: ConversationDraftMessage[]) => {
      let changed = false
      const next = messages.map((message: ConversationDraftMessage) => ({
        ...message,
        pieces: message.pieces.map((piece: ConversationDraftPiece) => {
          if (piece.draftId !== pieceId) return piece
          const updated = update(piece)
          changed ||= updated !== piece
          return updated
        }),
      }))
      return changed ? next : messages
    })
  }, [changeMessages])
  const changeAttachments = useCallback((pieceId: string, attachments: MessageAttachment[]): void => {
    for (const attachment of attachments) if (attachment.file) urls.current.add(attachment.url)
    changeMessages((messages: ConversationDraftMessage[]) => {
      const owner = messages.find((message: ConversationDraftMessage) => message.pieces.some(
        (piece: ConversationDraftPiece) => piece.draftId === pieceId,
      ))
      const piece = owner?.pieces.find((candidate: ConversationDraftPiece) => candidate.draftId === pieceId)
      if (!owner || !piece) return messages
      const additions = attachments.filter((attachment: MessageAttachment) => !owner.pieces.some(
        (candidate: ConversationDraftPiece) => candidate.draftId === attachment.draftId,
      )).map(attachmentDraft)
      const removed = isPathDataType(piece.data_type)
        && !attachments.some((attachment: MessageAttachment) => attachment.draftId === pieceId)
      if (!removed && !additions.length) return messages
      return messages.map((message: ConversationDraftMessage) => {
        if (message.id !== owner.id) return message
        const pieces = [...message.pieces.filter((candidate: ConversationDraftPiece) => !removed || candidate.draftId !== pieceId), ...additions]
        return { ...message, pieces: pieces.length ? pieces : [newDraftPiece()] }
      })
    })
  }, [changeMessages])
  const addPiece = (messageId: string, type: string): void => {
    changeMessages((messages: ConversationDraftMessage[]) => {
      const piece = newDraftPiece(type)
      if (type === 'function_call') piece.original_value = JSON.stringify({ type: 'function_call', call_id: '', name: '', arguments: '{}' })
      if (type === 'function_call_output') {
        const callId = unansweredToolCallId(messages, messageId)
        if (!callId) return messages
        piece.original_value = JSON.stringify({ type: 'function_call_output', call_id: callId, output: '' })
        const index = messages.findIndex((message: ConversationDraftMessage) => message.id === messageId)
        return [...messages.slice(0, index + 1), { id: generateClientId(), role: 'tool', pieces: [piece] }, ...messages.slice(index + 1)]
      }
      return messages.map((message: ConversationDraftMessage) => message.id === messageId
        ? { ...message, pieces: [...message.pieces, piece] } : message)
    })
  }
  const applyConversions = (applied: Record<string, PieceConversion>): void => {
    changeMessages((messages: ConversationDraftMessage[]) => messages.map((message: ConversationDraftMessage) => ({
      ...message, pieces: message.pieces.map((piece: ConversationDraftPiece) => {
        const result = applied[piece.draftId]
        return result ? {
          ...piece, converted_value: result.convertedValue,
          converted_value_data_type: result.convertedDataType,
          applied_converter_ids: result.converterInstanceIds,
        } : piece
      }),
    })))
  }
  const save = async (
    destination: SaveConversationRequest['destination'], onSaved: (response: AddMessageResponse) => void,
  ): Promise<void> => {
    if (!draft || workflow.saving) return
    const id = draft.id
    setError(null)
    try {
      if (validationError || targetError) throw new Error(validationError ?? targetError)
      const response = await workflow.save(draft, destination)
      if (activeId.current !== id) return
      saved.current = true
      onSaved(response)
    } catch (cause) {
      if (activeId.current === id) setError(toApiError(cause).detail)
    }
  }

  const changeObjective = useCallback((objective: string): void => {
    setDraft((current: DraftState | null) => current ? { ...current, objective } : current)
  }, [])

  return {
    draft, dirty, error, validationError, targetError, saving: workflow.saving,
    begin, discard, save, changeMessages, changePiece, changeAttachments, addPiece, applyConversions,
    shouldBlock: (): boolean => !saved.current && (dirty || workflow.saving),
    changeObjective,
    changeTarget: (target: TargetInstance | null): void => {
      setDraft((current: DraftState | null) => current ? { ...current, target } : current)
    },
    insert: (index: number): void => {
      const message = newDraftMessage()
      changeMessages((messages: ConversationDraftMessage[]) => [...messages.slice(0, index), message, ...messages.slice(index)])
    },
    removeMessage: (messageId: string): void => {
      changeMessages((messages: ConversationDraftMessage[]) => messages.filter((message: ConversationDraftMessage) => message.id !== messageId))
    },
  }
}
