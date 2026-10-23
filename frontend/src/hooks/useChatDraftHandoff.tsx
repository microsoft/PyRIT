import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

import { generateClientId } from '@/utils/clientId'
import {
  canApplyPendingImport,
  isChatDraftDirty,
  releaseSupersededUrls,
  revokeAttachmentUrls,
  shouldApplyPreservedDraft,
  type ChatDraftContent,
  type ChatDraftContextState,
  type ChatDraftHandoffContextValue,
  type PendingImportPayload,
} from '@/utils/chatDraft'


const ChatDraftHandoffContext = createContext<ChatDraftHandoffContextValue | null>(null)

export function ChatDraftProvider({ children }: { children: ReactNode }) {
  const [preservedDraft, setPreservedDraft] = useState<ChatDraftContextState | null>(null)
  const [pendingImport, setPendingImport] = useState<PendingImportPayload | null>(null)
  const [activeSequence, setActiveSequence] = useState(0)
  const sequenceRef = useRef(0)
  const preservedDraftRef = useRef(preservedDraft)
  const pendingImportRef = useRef(pendingImport)

  useEffect(() => {
    preservedDraftRef.current = preservedDraft
  }, [preservedDraft])

  useEffect(() => {
    pendingImportRef.current = pendingImport
  }, [pendingImport])

  // Revoke any leftover blob URLs on unmount of provider
  useEffect(() => {
    return () => {
      if (preservedDraftRef.current?.draft?.attachments) {
        revokeAttachmentUrls(preservedDraftRef.current.draft.attachments)
      }
      if (pendingImportRef.current?.content?.attachments) {
        revokeAttachmentUrls(pendingImportRef.current.content.attachments)
      }
    }
  }, [])

  const preserveDraft = useCallback((state: Omit<ChatDraftContextState, 'savedAt' | 'sequence'>) => {
    sequenceRef.current += 1
    const nextSeq = sequenceRef.current
    setActiveSequence(nextSeq)
    const prevAttachments = preservedDraftRef.current?.draft?.attachments ?? []
    releaseSupersededUrls(prevAttachments, state.draft?.attachments ?? [])
    const nextState: ChatDraftContextState = {
      ...state,
      savedAt: Date.now(),
      sequence: nextSeq,
    }
    preservedDraftRef.current = nextState
    setPreservedDraft(nextState)
  }, [])

  const clearPreservedDraft = useCallback(() => {
    const current = preservedDraftRef.current
    if (current?.draft?.attachments) {
      // Do not revoke if pendingImport is holding the same attachment
      const pendingUrls = new Set(pendingImportRef.current?.content?.attachments?.map((a) => a.url) ?? [])
      for (const att of current.draft.attachments) {
        if (att.url && att.url.startsWith('blob:') && !pendingUrls.has(att.url)) {
          try {
            URL.revokeObjectURL(att.url)
          } catch {
            // ignore
          }
        }
      }
    }
    preservedDraftRef.current = null
    setPreservedDraft(null)
  }, [])

  const stagePendingImport = useCallback((payload: Omit<PendingImportPayload, 'id' | 'sequence' | 'stagedAt'>): PendingImportPayload => {
    sequenceRef.current += 1
    const nextSeq = sequenceRef.current
    setActiveSequence(nextSeq)
    const prevImportAttachments = pendingImportRef.current?.content?.attachments ?? []
    releaseSupersededUrls(prevImportAttachments, payload.content?.attachments ?? [])
    const staged: PendingImportPayload = {
      ...payload,
      id: generateClientId(),
      sequence: nextSeq,
      stagedAt: Date.now(),
    }
    pendingImportRef.current = staged
    setPendingImport(staged)
    return staged
  }, [])

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
      revokeAttachmentUrls(current.content?.attachments)
      setPendingImport(null)
      pendingImportRef.current = null
      return null
    }
    // Verify destination matches
    if (id && !canApplyPendingImport(current, currentAttackId, currentConversationId)) {
      return null
    }
    // Consumed: clear pending import so it cannot be applied twice
    setPendingImport(null)
    pendingImportRef.current = null
    return current
  }, [])

  const dismissPendingImport = useCallback((id?: string) => {
    const current = pendingImportRef.current
    if (!current || (id && current.id !== id)) return
    const preservedUrls = new Set(preservedDraftRef.current?.draft?.attachments?.map((a) => a.url) ?? [])
    for (const att of current.content?.attachments ?? []) {
      if (att.url && att.url.startsWith('blob:') && !preservedUrls.has(att.url)) {
        try {
          URL.revokeObjectURL(att.url)
        } catch {
          // ignore
        }
      }
    }
    pendingImportRef.current = null
    setPendingImport(null)
  }, [])

  const consumePreservedDraft = useCallback((
    currentAttackId: string | null,
    currentConversationId: string | null = null,
  ): ChatDraftContent | null => {
    const current = preservedDraftRef.current
    if (!current) return null
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
  stagePendingImport: (payload) => ({ ...payload, id: 'fallback-id', sequence: 0, stagedAt: Date.now() }),
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
