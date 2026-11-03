import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

import type {
  ChatDraftContent, ChatDraftContextState, ChatDraftHandoffContextValue, MessageAttachment, PendingImportPayload,
} from '@/types'
import { generateClientId } from '@/utils/clientId'
import {
  canApplyPendingImport,
  isChatDraftDirty,
  releaseSupersededUrls,
  revokeAttachmentUrls,
  shouldApplyPreservedDraft,
} from '@/utils/chatDraft'

const ChatDraftHandoffContext = createContext<ChatDraftHandoffContextValue | null>(null)

function allDraftAttachments(draft?: ChatDraftContent | null): MessageAttachment[] {
  if (!draft) return []
  return [
    ...draft.attachments ?? [],
    ...draft.conversation?.pendingComposerDraft?.attachments ?? [],
  ]
}

function allImportAttachments(payload?: PendingImportPayload | null): MessageAttachment[] {
  if (!payload) return []
  return [
    ...payload.content?.attachments ?? [],
    ...payload.conversation?.composerDraft?.attachments ?? [],
  ]
}

export function ChatDraftProvider({ children }: { children: ReactNode }) {
  const [preservedDraft, setPreservedDraft] = useState<ChatDraftContextState | null>(null)
  const [pendingImport, setPendingImport] = useState<PendingImportPayload | null>(null)
  const [activeSequence, setActiveSequence] = useState(0)
  const sequenceRef = useRef(0)
  const preservedDraftRef = useRef<ChatDraftContextState | null>(null)
  const pendingImportRef = useRef<PendingImportPayload | null>(null)
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      revokeAttachmentUrls([
        ...allDraftAttachments(preservedDraftRef.current?.draft),
        ...allImportAttachments(pendingImportRef.current),
      ])
      preservedDraftRef.current = null
      pendingImportRef.current = null
    }
  }, [])

  const advanceSequence = useCallback((): number => {
    sequenceRef.current += 1
    setActiveSequence(sequenceRef.current)
    return sequenceRef.current
  }, [])

  const preserveDraft = useCallback((state: Omit<ChatDraftContextState, 'savedAt' | 'sequence'>) => {
    // Parent effects are cleaned up before the active composer's unmount.
    if (!mountedRef.current) {
      revokeAttachmentUrls(allDraftAttachments(state.draft))
      return
    }
    releaseSupersededUrls(allDraftAttachments(preservedDraftRef.current?.draft), [
      ...allDraftAttachments(state.draft),
      ...allImportAttachments(pendingImportRef.current),
    ])
    const nextState: ChatDraftContextState = {
      ...state,
      savedAt: Date.now(),
      sequence: advanceSequence(),
    }
    preservedDraftRef.current = nextState
    setPreservedDraft(nextState)
  }, [advanceSequence])

  const clearPreservedDraft = useCallback((expectedSequence?: number) => {
    const current = preservedDraftRef.current
    if (expectedSequence !== undefined && current?.sequence !== expectedSequence) return
    releaseSupersededUrls(
      allDraftAttachments(current?.draft),
      allImportAttachments(pendingImportRef.current),
    )
    preservedDraftRef.current = null
    setPreservedDraft(null)
    advanceSequence()
  }, [advanceSequence])

  const stagePendingImport = useCallback((
    payload: Omit<PendingImportPayload, 'id' | 'sequence' | 'stagedAt'>,
    expectedSequence?: number,
  ): PendingImportPayload | null => {
    const incomingAttachments = [
      ...payload.content.attachments ?? [],
      ...payload.conversation?.composerDraft?.attachments ?? [],
    ]
    if (!mountedRef.current || (expectedSequence !== undefined && expectedSequence !== sequenceRef.current)) {
      releaseSupersededUrls(incomingAttachments, [
        ...allDraftAttachments(preservedDraftRef.current?.draft),
        ...allImportAttachments(pendingImportRef.current),
      ])
      return null
    }
    releaseSupersededUrls(allImportAttachments(pendingImportRef.current), [
      ...incomingAttachments,
      ...allDraftAttachments(preservedDraftRef.current?.draft),
    ])
    const staged: PendingImportPayload = {
      ...payload,
      id: generateClientId(),
      sequence: advanceSequence(),
      stagedAt: Date.now(),
    }
    pendingImportRef.current = staged
    setPendingImport(staged)
    return staged
  }, [advanceSequence])

  const consumePendingImport = useCallback((
    id?: string,
    currentAttackId: string | null = null,
    currentConversationId: string | null = null,
  ): PendingImportPayload | null => {
    const current = pendingImportRef.current
    if (!current) return null
    if (id && current.id !== id) return null
    // Guard against stale asynchronous completion
    if (current.sequence < sequenceRef.current) {
      releaseSupersededUrls(allImportAttachments(current), allDraftAttachments(preservedDraftRef.current?.draft))
      setPendingImport(null)
      pendingImportRef.current = null
      return null
    }
    // Verify destination matches
    if (!canApplyPendingImport(current, currentAttackId, currentConversationId)) {
      return null
    }
    // Consumed: clear pending import so it cannot be applied twice
    setPendingImport(null)
    pendingImportRef.current = null
    return current
  }, [])

  const dismissPendingImport = useCallback((id?: string) => {
    const current = pendingImportRef.current
    if (id && current?.id !== id) return
    releaseSupersededUrls(
      allImportAttachments(current),
      allDraftAttachments(preservedDraftRef.current?.draft),
    )
    pendingImportRef.current = null
    setPendingImport(null)
    advanceSequence()
  }, [advanceSequence])

  const consumePreservedDraft = useCallback((
    currentAttackId: string | null,
    currentConversationId: string | null = null,
    expectedSequence?: number,
  ): ChatDraftContent | null => {
    const current = preservedDraftRef.current
    if (!current) return null
    if (expectedSequence !== undefined && current.sequence !== expectedSequence) return null
    if (!shouldApplyPreservedDraft(current, currentAttackId, currentConversationId)) {
      return null
    }
    // Return draft content without revoking attachments (ownership transfers to ChatWindow)
    setPreservedDraft(null)
    preservedDraftRef.current = null
    return current.draft
  }, [])

  const value = useMemo<ChatDraftHandoffContextValue>(() => ({
    preservedDraft,
    pendingImport,
    activeSequence,
    preserveDraft,
    clearPreservedDraft,
    stagePendingImport,
    consumePendingImport,
    dismissPendingImport,
    consumePreservedDraft,
    isDraftDirty: isChatDraftDirty,
  }), [
    preservedDraft,
    pendingImport,
    activeSequence,
    preserveDraft,
    clearPreservedDraft,
    stagePendingImport,
    consumePendingImport,
    dismissPendingImport,
    consumePreservedDraft,
  ])

  return (
    <ChatDraftHandoffContext.Provider value={value}>
      {children}
    </ChatDraftHandoffContext.Provider>
  )
}

const DEFAULT_CHAT_DRAFT_HANDOFF: ChatDraftHandoffContextValue = {
  preservedDraft: null,
  pendingImport: null,
  activeSequence: 0,
  preserveDraft: () => {},
  clearPreservedDraft: () => {},
  stagePendingImport: () => { throw new Error('Chat imports require a ChatDraftProvider.') },
  consumePendingImport: () => null,
  dismissPendingImport: () => {},
  consumePreservedDraft: () => null,
  isDraftDirty: isChatDraftDirty,
}

// eslint-disable-next-line react-refresh/only-export-components
export function useChatDraftHandoff(): ChatDraftHandoffContextValue {
  const context = useContext(ChatDraftHandoffContext)
  return context ?? DEFAULT_CHAT_DRAFT_HANDOFF
}
