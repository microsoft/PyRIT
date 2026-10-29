import { useCallback, useEffect, useRef, useState } from 'react'

import { toApiError } from '@/services/errors'
import { buildTemperatureTarget } from '@/services/targetRegistry'
import type {
  AddMessageResponse, ConversationDraftMessage, ConversationDraftPiece, ConversationSaveInput,
  MessageAttachment, NewAttackContext, PieceConversionUpdates, SaveConversationRequest, TargetInstance,
} from '@/types'
import { generateClientId } from '@/utils/clientId'
import {
  attachmentDraft, draftDataTypes, editorTargetDisabledReason, newDraftMessage, newDraftPiece,
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
  temperature: string
}

export function useConversationDraft(
  newAttackContext?: NewAttackContext, options: { pendingUserMessage?: boolean } = {},
) {
  const [draft, setDraft] = useState<DraftState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [preparing, setPreparing] = useState(false)
  const savePending = useRef(false)
  const activeId = useRef<string | null>(null)
  const saved = useRef(false)
  const urls = useRef(new Set<string>())
  const workflow = useConversationSave(newAttackContext)
  const dirty = draft !== null && (
    draft.messages !== draft.baselineMessages || draft.objective !== draft.initialObjective
    || targetKey(draft.target) !== draft.baselineTarget || draft.temperature.trim() !== ''
  )
  const validationError = draft ? validateDraft(draft.messages) : null
  const messages = draft?.messages ?? []
  const history = options.pendingUserMessage && messages[messages.length - 1]?.role === 'user'
    ? messages.slice(0, -1) : messages
  const targetError = draft?.target
    ? editorTargetDisabledReason(draft.target, draftDataTypes(messages), history) : undefined

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
    setDraft({
      ...input, id, baselineMessages: input.messages, baselineTarget: targetKey(input.target), temperature: '',
      sourcePieceIds: input.messages.flatMap((message: ConversationDraftMessage) => message.pieces.flatMap(
        (piece: ConversationDraftPiece) => piece.source_piece_id ? [piece.source_piece_id] : [],
      )),
    })
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
        return [...messages.slice(0, index + 1), { id: generateClientId(), role: 'simulated_tool', pieces: [piece] }, ...messages.slice(index + 1)]
      }
      return messages.map((message: ConversationDraftMessage) => message.id === messageId
        ? { ...message, pieces: [...message.pieces, piece] } : message)
    })
  }
  const applyConversions = (applied: PieceConversionUpdates): void => {
    changeMessages((messages: ConversationDraftMessage[]) => {
      let changed = false
      const next = messages.map((message: ConversationDraftMessage) => ({
        ...message, pieces: message.pieces.map((piece: ConversationDraftPiece) => {
          if (!Object.prototype.hasOwnProperty.call(applied, piece.draftId)) return piece
          const result = applied[piece.draftId]
          changed = true
          return {
            ...piece, converted_value: result?.convertedValue,
            converted_value_data_type: result?.convertedDataType,
            applied_converter_ids: result?.converterInstanceIds ?? [],
            applied_converter_provenance: result?.converterProvenance,
          }
        }),
      }))
      return changed ? next : messages
    })
  }
  const save = async (
    destination: SaveConversationRequest['destination'], onSaved: (response: AddMessageResponse, target?: TargetInstance | null) => void,
    messages: ConversationDraftMessage[] = draft?.messages ?? [],
    reuseUnsentConversation = false,
  ): Promise<boolean> => {
    if (!draft || workflow.saving || savePending.current) return false
    savePending.current = true
    setPreparing(true)
    const id = draft.id
    setError(null)
    try {
      if (validationError || targetError) throw new Error(validationError ?? targetError)
      if (destination === 'new_attack' && newAttackContext && !newAttackContext.ready) {
        throw new Error('Default labels are not ready. Retry after default labels finish loading.')
      }
      let target = draft.target
      if (draft.temperature.trim()) {
        if (destination !== 'new_attack') throw new Error('Choose New attack to change the temperature.')
        if (!target) throw new Error('Select a target before setting temperature.')
        target = await buildTemperatureTarget(target, Number(draft.temperature))
      }
      if (activeId.current !== id) return false
      const response = await workflow.save(
        { ...draft, target, messages }, destination, reuseUnsentConversation, newAttackContext,
      )
      if (activeId.current !== id) return false
      saved.current = true
      if (draft.temperature.trim()) onSaved(response, target)
      else onSaved(response)
      return true
    } catch (cause) {
      if (activeId.current === id) setError(toApiError(cause).detail)
      return false
    } finally {
      savePending.current = false
      setPreparing(false)
    }
  }

  const changeObjective = useCallback((objective: string): void => {
    setDraft((current: DraftState | null) => current ? { ...current, objective } : current)
  }, [])

  return {
    draft, dirty, error, validationError, targetError, history, saving: preparing || workflow.saving,
    begin, discard, save, changeMessages, changePiece, changeAttachments, addPiece, applyConversions,
    shouldBlock: (): boolean => !saved.current && (dirty || preparing || workflow.saving),
    changeObjective,
    changeTemperature: (temperature: string): void => {
      setDraft((current: DraftState | null) => current ? { ...current, temperature } : current)
    },
    changeTarget: (target: TargetInstance | null): void => {
      setDraft((current: DraftState | null) => current ? { ...current, target, temperature: '' } : current)
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
