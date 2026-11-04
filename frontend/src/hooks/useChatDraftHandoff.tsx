import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

import type {
  ChatDraftContent, ChatDraftContextState, ChatDraftHandoffContextValue, ConversationDraftMessage,
  ConversationDraftPiece, MessageAttachment, PendingImportPayload,
} from '@/types'
import { generateClientId } from '@/utils/clientId'
import { draftAttachment } from '@/utils/conversationDraft'
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
    ...draft.conversation?.messages.flatMap((message: ConversationDraftMessage) => message.pieces
      .filter((piece: ConversationDraftPiece) => piece.previewUrl?.startsWith('blob:')).map(draftAttachment)) ?? [],
  ]
}

function draftKey(attackId: string | null = null, conversationId: string | null = null): string {
  return `${attackId ?? ''}:${conversationId ?? ''}`
}

function allDraftsAttachments(drafts: Map<string, ChatDraftContextState>): MessageAttachment[] {
  return Array.from(drafts.values()).flatMap((d: ChatDraftContextState) => allDraftAttachments(d.draft))
}

function allImportAttachments(payload?: Pick<PendingImportPayload, 'content' | 'conversation'> | null): MessageAttachment[] {
  if (!payload) return []
  return [
    ...payload.content?.attachments ?? [],
    ...payload.conversation?.composerDraft?.attachments ?? [],
    ...payload.conversation?.messages.flatMap((message: ConversationDraftMessage) => message.pieces
      .filter((piece: ConversationDraftPiece) => piece.previewUrl?.startsWith('blob:')).map(draftAttachment)) ?? [],
  ]
}

export function ChatDraftProvider({ children }: { children: ReactNode }) {
  const [preservedDraft, setPreservedDraft] = useState<ChatDraftContextState | null>(null)
  const [pendingImport, setPendingImport] = useState<PendingImportPayload | null>(null)
  const [activeSequence, setActiveSequence] = useState(0)
  const sequenceRef = useRef(0)
  const preservedDraftRef = useRef<ChatDraftContextState | null>(null)
  const preservedDraftsRef = useRef<Map<string, ChatDraftContextState>>(new Map())
  const pendingImportRef = useRef<PendingImportPayload | null>(null)
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true
    const preservedDrafts = preservedDraftsRef.current
    return () => {
      mountedRef.current = false
      revokeAttachmentUrls([
        ...allDraftsAttachments(preservedDrafts),
        ...allImportAttachments(pendingImportRef.current),
      ])
      preservedDrafts.clear()
      preservedDraftRef.current = null
      pendingImportRef.current = null
    }
  }, [])

  const advanceSequence = useCallback((): number => {
    sequenceRef.current += 1
    setActiveSequence(sequenceRef.current)
    return sequenceRef.current
  }, [])

  const preserveDraft = useCallback((
    state: Omit<ChatDraftContextState, 'savedAt' | 'sequence'>,
    expectedSequence?: number,
  ) => {
    // Parent effects are cleaned up before the active composer's unmount.
    if (!mountedRef.current) {
      revokeAttachmentUrls(allDraftAttachments(state.draft))
      return
    }
    const key = draftKey(state.originatingAttackId, state.originatingConversationId)
    // A new attack must not restore the old composer's unmount snapshot.
    if (expectedSequence !== undefined && expectedSequence !== sequenceRef.current) {
      releaseSupersededUrls(allDraftAttachments(state.draft), [
        ...allDraftsAttachments(preservedDraftsRef.current),
        ...allImportAttachments(pendingImportRef.current),
      ])
      return
    }
    const existingForConv = preservedDraftsRef.current.get(key)
    releaseSupersededUrls(allDraftAttachments(existingForConv?.draft), [
      ...allDraftAttachments(state.draft),
      ...allImportAttachments(pendingImportRef.current),
    ])
    const nextState: ChatDraftContextState = {
      ...state,
      savedAt: Date.now(),
      sequence: advanceSequence(),
    }
    preservedDraftsRef.current.set(key, nextState)
    preservedDraftRef.current = nextState
    setPreservedDraft(nextState)
  }, [advanceSequence])

  const clearPreservedDraft = useCallback((
    expectedSequence?: number,
    targetAttackId?: string | null,
    targetConversationId?: string | null,
  ) => {
    if (targetAttackId !== undefined) {
      const key = draftKey(targetAttackId, targetConversationId)
      const current = preservedDraftsRef.current.get(key)
      if (!current) return
      if (expectedSequence !== undefined && current.sequence !== expectedSequence) return
      releaseSupersededUrls(
        allDraftAttachments(current.draft),
        allImportAttachments(pendingImportRef.current),
      )
      preservedDraftsRef.current.delete(key)
      if (preservedDraftRef.current?.sequence === current.sequence) {
        const remaining = Array.from(preservedDraftsRef.current.values())
        const nextActive = remaining.length > 0 ? remaining[remaining.length - 1] : null
        preservedDraftRef.current = nextActive
        setPreservedDraft(nextActive)
      }
      advanceSequence()
      return
    }

    const current = preservedDraftRef.current
    if (expectedSequence !== undefined && current?.sequence !== expectedSequence) return
    releaseSupersededUrls(
      allDraftsAttachments(preservedDraftsRef.current),
      allImportAttachments(pendingImportRef.current),
    )
    preservedDraftsRef.current.clear()
    preservedDraftRef.current = null
    setPreservedDraft(null)
    advanceSequence()
  }, [advanceSequence])

  const stagePendingImport = useCallback((
    payload: Omit<PendingImportPayload, 'id' | 'sequence' | 'stagedAt'>,
    expectedSequence?: number,
  ): PendingImportPayload | null => {
    const incomingAttachments = allImportAttachments(payload)
    if (!mountedRef.current || (expectedSequence !== undefined && expectedSequence !== sequenceRef.current)) {
      releaseSupersededUrls(incomingAttachments, [
        ...allDraftsAttachments(preservedDraftsRef.current),
        ...allImportAttachments(pendingImportRef.current),
      ])
      return null
    }
    releaseSupersededUrls(allImportAttachments(pendingImportRef.current), [
      ...incomingAttachments,
      ...allDraftsAttachments(preservedDraftsRef.current),
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
      releaseSupersededUrls(allImportAttachments(current), allDraftsAttachments(preservedDraftsRef.current))
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
      allDraftsAttachments(preservedDraftsRef.current),
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
    const key = draftKey(currentAttackId, currentConversationId)
    const current = preservedDraftsRef.current.get(key) ?? preservedDraftRef.current
    if (!current) return null
    if (expectedSequence !== undefined && current.sequence !== expectedSequence) return null
    if (!shouldApplyPreservedDraft(current, currentAttackId, currentConversationId)) {
      return null
    }
    // Return draft content without revoking attachments (ownership transfers to ChatWindow)
    preservedDraftsRef.current.delete(key)
    if (preservedDraftRef.current?.sequence === current.sequence) {
      const remaining = Array.from(preservedDraftsRef.current.values())
      const nextActive = remaining.length > 0 ? remaining[remaining.length - 1] : null
      preservedDraftRef.current = nextActive
      setPreservedDraft(nextActive)
    }
    return current.draft
  }, [])

  const getPreservedDraft = useCallback((
    currentAttackId?: string | null,
    currentConversationId?: string | null,
  ): ChatDraftContextState | null => {
    if (currentAttackId === undefined && currentConversationId === undefined) {
      return preservedDraftRef.current
    }
    const key = draftKey(currentAttackId ?? null, currentConversationId ?? null)
    return preservedDraftsRef.current.get(key) ?? null
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
    getPreservedDraft,
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
    getPreservedDraft,
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
  getPreservedDraft: () => null,
  isDraftDirty: isChatDraftDirty,
}

// eslint-disable-next-line react-refresh/only-export-components
export function useChatDraftHandoff(): ChatDraftHandoffContextValue {
  const context = useContext(ChatDraftHandoffContext)
  return context ?? DEFAULT_CHAT_DRAFT_HANDOFF
}
