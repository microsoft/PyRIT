import { useRef } from 'react'

import { useConversationSave } from '@/hooks/useConversationSave'
import { attacksApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type {
  AttackSummary,
  AttackTargetResolutionStatus,
  BackendMessage,
  BackendMessagePiece,
  ChatConverterController,
  ChatConverterSnapshot,
  ConversationDraftPiece,
  ConversationMessagesResponse,
  Message,
  MessageAttachment,
  MessageDisplayPiece,
  NewAttackContext,
  RegisteredComponentReference,
  TargetInstance,
} from '@/types'
import {
  canBranchFirstPrompt, draftDataTypes, draftMessageToComposer, editorTargetDisabledReason,
  historyReplayDisabledReason, toConversationDraft,
} from '@/utils/conversationDraft'
import { conversationPieceSignature, preparePromptRestore } from '@/utils/promptRestore'

export type ConversationCopyDestination = 'same_attack' | 'new_attack' | 'technique'

export type RestoredPrompt = ReturnType<typeof draftMessageToComposer>

/** The failed send that can be recovered or branched from. */
export interface CopyRecoverableSend {
  history: Array<Pick<BackendMessage, 'role'>>
  historyCutoffIndex: number
  failedRequestMessageIndex: number
}

/** The first prompt sent in the viewed conversation, kept so a branch can restore it into the composer. */
export interface CopyFirstPromptSend {
  historySignature: string
  cutoffIndex: number
  originalValue: string
  textMetadata?: Record<string, unknown>
  attachments: MessageAttachment[]
  converters: ChatConverterSnapshot
}

export interface CopyLaunchState {
  generation: string
  ready: boolean
  defaultsReady: boolean
  scorerSelectionReady: boolean
}

export interface UseConversationCopyOptions {
  attackResultId: string | null
  viewedConversationId: string | null
  messages: Message[]
  activeTarget: TargetInstance | null
  targetResolutionStatus: AttackTargetResolutionStatus | undefined
  objective: string
  labels?: Record<string, string>
  defaultChatScorer?: RegisteredComponentReference | null
  newAttackContext?: NewAttackContext
  runtimeGeneration: string
  runtimeReady: boolean
  defaultsReady: boolean
  scorerSelectionReady: boolean
  executionReadOnly: boolean
  isMutationLocked: boolean
  /** True while a send or another conversation edit is in flight. */
  isBusy: boolean
  recoverableSend?: CopyRecoverableSend
  firstPromptSend?: CopyFirstPromptSend
  converters: ChatConverterController
  promptRestoreDisabledReason: (snapshot?: ChatConverterSnapshot) => string | undefined
  /** Live reads used to detect that the user moved on while the copy was loading. */
  getViewedConversationId: () => string | null
  getViewedAttackId: () => string | null
  getNavigationRevision: () => number
  getDraftRevision: () => number | undefined
  getLaunchState: () => CopyLaunchState
  getConverters: () => ChatConverterController
  setBusy: (busy: boolean) => void
  setEditorError: (error: string | null) => void
  setEditorNotice: (notice: string | null) => void
  recoverProcessingError: () => Promise<void>
  restoreRecoverableDraft: () => void
  restoreEditorPrompt: (prompt: RestoredPrompt | undefined, snapshot: ChatConverterSnapshot) => void
  clearUndoableSend: () => void
  showConversation: (response: ConversationMessagesResponse) => void
  refreshPanel: () => void
  openPanel: () => void
  navigate: (path: string) => void
  onSelectConversation: (conversationId: string) => void
  onConversationCreated: (
    attackResultId: string, conversationId: string, objective?: string, target?: TargetInstance | null,
  ) => void
  onAttackChange?: (attack: AttackSummary) => void
}

export interface ConversationCopy {
  /** Copies the conversation up to a message into the same attack, a new attack, or a technique run. */
  copyConversation: (messageIndex: number, destination: ConversationCopyDestination) => Promise<void>
  /** Why branching from a message is unavailable, if it is. */
  branchDisabledReason: (messageIndex: number) => string | undefined
}

/** Owns copy-to-conversation and branch workflows, including the lock that prevents overlapping copies. */
export function useConversationCopy(options: UseConversationCopyOptions): ConversationCopy {
  const copyingRef = useRef(false)
  const copySave = useConversationSave(options.newAttackContext)

  const copyConversation = async (
    messageIndex: number, destination: ConversationCopyDestination,
  ): Promise<void> => {
    const {
      attackResultId, viewedConversationId, messages, activeTarget, targetResolutionStatus, objective, labels,
      defaultChatScorer, runtimeGeneration, executionReadOnly, recoverableSend, firstPromptSend, converters,
      getViewedConversationId, getViewedAttackId, getNavigationRevision, getDraftRevision, getLaunchState, getConverters,
    } = options
    if (!attackResultId || !viewedConversationId || copyingRef.current || options.isBusy) return
    if (destination === 'same_attack' && options.isMutationLocked) return
    if (destination !== 'same_attack' && (!options.runtimeReady || !options.defaultsReady || !options.scorerSelectionReady)) return
    if (destination === 'same_attack' && recoverableSend
      && messageIndex === recoverableSend.failedRequestMessageIndex) {
      await options.recoverProcessingError()
      return
    }
    const sourceId = viewedConversationId
    const draftRevision = getDraftRevision()
    const navigationRevision = getNavigationRevision()
    const branchConverterSnapshot = converters.snapshot()
    const selectedPieceIds = messages[messageIndex]?.displayPieces?.map((piece: MessageDisplayPiece) => piece.pieceId) ?? []
    const restoresFirstPrompt = destination === 'same_attack' && activeTarget
      && canBranchFirstPrompt(activeTarget, messages, messageIndex)
    const draftUnchanged = (): boolean => {
      const launch = getLaunchState()
      return getViewedAttackId() === attackResultId
        && getViewedConversationId() === sourceId
        && getNavigationRevision() === navigationRevision
        && getDraftRevision() === draftRevision
        && launch.generation === runtimeGeneration && launch.ready
        && !getConverters().isConverting
        && JSON.stringify(getConverters().snapshot()) === JSON.stringify(branchConverterSnapshot)
    }
    copyingRef.current = true
    options.setBusy(true)
    options.setEditorError(null)
    options.setEditorNotice(null)
    try {
      const source = await attacksApi.getMessages(attackResultId, sourceId)
      const launch = getLaunchState()
      if (destination !== 'same_attack' && (launch.generation !== runtimeGeneration || !launch.ready || !launch.defaultsReady
        || !launch.scorerSelectionReady)) {
        throw new Error('Runtime or default labels changed while loading this conversation. Retry after default labels finish loading.')
      }
      const isFailedPrompt = destination === 'new_attack' && recoverableSend && messageIndex === recoverableSend.failedRequestMessageIndex
      const sourceMessages = isFailedPrompt
        ? source.messages.filter((message: BackendMessage) => message.turn_number <= recoverableSend.historyCutoffIndex)
        : source.messages.slice(0, messageIndex + 1)
      let copiedMessages = toConversationDraft(sourceMessages)
      let restoredPrompt: RestoredPrompt | undefined
      let promptSnapshot: ChatConverterSnapshot | undefined
      if (restoresFirstPrompt && activeTarget) {
        const selected = source.messages[messageIndex]
        if (!canBranchFirstPrompt(activeTarget, source.messages, messageIndex)
          || selectedPieceIds.some((id: string) => !selected.message_pieces.some((piece: BackendMessagePiece) => piece.id === id))) {
          throw new Error('The selected prompt changed. Reload the conversation before branching.')
        }
        if (!draftUnchanged()) throw new Error('The view, prompt, or runtime changed. Your current draft was not replaced.')
        const saved = firstPromptSend?.cutoffIndex === selected.turn_number - 1
          && firstPromptSend.historySignature === conversationPieceSignature(source) ? firstPromptSend : undefined
        const restoreError = options.promptRestoreDisabledReason(saved?.converters)
        if (restoreError) throw new Error(restoreError)
        const restoration = preparePromptRestore(saved?.converters, branchConverterSnapshot)
        promptSnapshot = restoration.snapshot
        const promptMessage = copiedMessages[copiedMessages.length - 1]
        restoredPrompt = saved ? {
          text: saved.originalValue, attachments: saved.attachments, textMetadata: saved.textMetadata, converters: promptSnapshot,
        } : draftMessageToComposer(restoration.pipelineChanged ? {
          ...promptMessage,
          pieces: promptMessage.pieces.map((piece: ConversationDraftPiece) => ({
            ...piece, converted_value: undefined, converted_value_data_type: undefined,
            applied_converter_ids: undefined, applied_converter_provenance: undefined,
          })),
        } : promptMessage, promptSnapshot)
        copiedMessages = copiedMessages.slice(0, -1)
      }
      if (isFailedPrompt && activeTarget
        && editorTargetDisabledReason(activeTarget, draftDataTypes(copiedMessages), copiedMessages)) {
        copiedMessages = []
      }
      if (destination === 'same_attack' && targetResolutionStatus !== 'unbound' && activeTarget) {
        const reason = editorTargetDisabledReason(activeTarget, draftDataTypes(copiedMessages), copiedMessages)
        if (reason) throw new Error(reason)
      }
      let target = activeTarget
      if (destination === 'technique' || (destination === 'same_attack' && targetResolutionStatus === 'unbound')) {
        target = null
      } else if (destination === 'new_attack' && activeTarget && !isFailedPrompt
        && editorTargetDisabledReason(activeTarget, draftDataTypes(copiedMessages), copiedMessages)) {
        target = null
      }
      if (isFailedPrompt && copiedMessages.length === 0 && !executionReadOnly) {
        const response = await attacksApi.createAttack({
          target_registry_name: target?.target_registry_name,
          objective: objective || undefined,
          labels,
          ...(defaultChatScorer === null || objective.trim() && defaultChatScorer
            ? { chat_scorer: defaultChatScorer } : {}),
        })
        if (getViewedConversationId() !== sourceId || getDraftRevision() !== draftRevision) return
        options.onConversationCreated(response.attack_result_id, response.conversation_id, objective, target)
        options.restoreRecoverableDraft()
        options.openPanel()
        options.refreshPanel()
        return
      }
      const response = await copySave.save({
        sourceAttackId: attackResultId,
        sourceConversationId: sourceId,
        initialObjective: objective,
        objective,
        target,
        labels,
        messages: copiedMessages,
      }, destination === 'same_attack' ? 'same_attack' : 'new_attack')
      options.refreshPanel()
      if (restoredPrompt && !draftUnchanged()) {
        options.setEditorNotice(`Branch created conversation ${response.messages.conversation_id} without replacing your current draft.`)
        return
      }
      if (getViewedConversationId() !== sourceId) return
      if (destination === 'technique') {
        const params = new URLSearchParams({ prepended_conversation_id: response.messages.conversation_id })
        options.navigate(`/scanner/launch/technique.execute?${params.toString()}`)
        return
      }
      if (isFailedPrompt && getDraftRevision() !== draftRevision) return
      if (destination === 'same_attack') {
        if (restoredPrompt) {
          options.showConversation(response.messages)
        }
        options.onSelectConversation(response.messages.conversation_id)
      }
      else options.onConversationCreated(response.attack.attack_result_id, response.messages.conversation_id, response.attack.objective, target)
      options.onAttackChange?.(response.attack)
      if (isFailedPrompt) options.restoreRecoverableDraft()
      if (restoredPrompt && promptSnapshot) {
        options.restoreEditorPrompt(restoredPrompt, promptSnapshot)
        options.clearUndoableSend()
      }
      options.openPanel()
    } catch (error) {
      if (getViewedConversationId() === sourceId) options.setEditorError(toApiError(error).detail)
    } finally {
      copyingRef.current = false
      options.setBusy(false)
    }
  }

  const branchDisabledReason = (index: number): string | undefined => {
    const { activeTarget, messages, recoverableSend, firstPromptSend, targetResolutionStatus } = options
    if (activeTarget && canBranchFirstPrompt(activeTarget, messages, index)
      && recoverableSend?.failedRequestMessageIndex !== index) {
      return options.promptRestoreDisabledReason(firstPromptSend?.converters)
    }
    if (targetResolutionStatus === 'unbound') return undefined
    if (!activeTarget) return 'Configure a target before branching this conversation.'
    if (recoverableSend?.failedRequestMessageIndex === index) {
      return historyReplayDisabledReason(
        activeTarget, activeTarget.capabilities?.supports_multi_turn ? recoverableSend.history : [],
      )
    }
    return historyReplayDisabledReason(activeTarget, messages.slice(0, index + 1))
  }

  return { copyConversation, branchDisabledReason }
}
