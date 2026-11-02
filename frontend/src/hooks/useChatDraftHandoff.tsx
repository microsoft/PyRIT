import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

import type {
  ChatDraftContent, ChatDraftContextState, ChatDraftHandoffContextValue, PendingImportPayload,
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
        ...preservedDraftRef.current?.draft.attachments ?? [],
        ...pendingImportRef.current?.content.attachments ?? [],
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
      revokeAttachmentUrls(state.draft.attachments)
      return
    }
    releaseSupersededUrls(preservedDraftRef.current?.draft.attachments, [
      ...state.draft.attachments,
      ...pendingImportRef.current?.content.attachments ?? [],
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
    releaseSupersededUrls(current?.draft.attachments, pendingImportRef.current?.content.attachments)
    preservedDraftRef.current = null
    setPreservedDraft(null)
    advanceSequence()
  }, [advanceSequence])

  const stagePendingImport = useCallback((
    payload: Omit<PendingImportPayload, 'id' | 'sequence' | 'stagedAt'>,
    expectedSequence?: number,
  ): PendingImportPayload | null => {
    if (!mountedRef.current || (expectedSequence !== undefined && expectedSequence !== sequenceRef.current)) {
      releaseSupersededUrls(payload.content.attachments, [
        ...preservedDraftRef.current?.draft.attachments ?? [],
        ...pendingImportRef.current?.content.attachments ?? [],
      ])
      return null
    }
    releaseSupersededUrls(pendingImportRef.current?.content.attachments, [
      ...payload.content.attachments ?? [],
      ...preservedDraftRef.current?.draft.attachments ?? [],
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
      releaseSupersededUrls(current.content.attachments, preservedDraftRef.current?.draft.attachments)
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
    releaseSupersededUrls(current?.content.attachments, preservedDraftRef.current?.draft.attachments)
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
