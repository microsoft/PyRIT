import type { ConverterPipelineStage, MessageAttachment, PieceConversion, TargetReference } from '@/types'

export interface ChatDraftConverters {
  applied: Record<string, PieceConversion>
  pipelines?: Record<string, ConverterPipelineStage[]>
}

export interface ChatDraftContent {
  text: string
  attachments: MessageAttachment[]
  systemPrompt?: string
  target?: TargetReference | null
  converters?: ChatDraftConverters
  textMetadata?: Record<string, unknown>
}

export interface ChatDraftContextState {
  /** Distinguishes unsaved chat (null) from a persisted attack (string) */
  originatingAttackId: string | null
  originatingConversationId: string | null
  originatingPath: string
  originatingState?: unknown
  draft: ChatDraftContent
  savedAt: number
  sequence: number
}

export type ImportDestinationType = 'new_attack' | 'current_conversation' | 'new_conversation' | 'any'

export interface PendingImportPayload {
  id: string
  sequence: number
  destinationType: ImportDestinationType
  targetAttackId?: string | null
  targetConversationId?: string | null
  content: {
    text: string
    attachments?: MessageAttachment[]
    textMetadata?: Record<string, unknown>
  }
  replaceDirty?: boolean
  focus?: boolean
  stagedAt: number
}

export interface ChatDraftHandoffContextValue {
  /** Currently preserved chat draft across navigation */
  preservedDraft: ChatDraftContextState | null
  /** Staged pending import from dataset explorer */
  pendingImport: PendingImportPayload | null
  /** Active monotonic sequence number for drafts/handoffs */
  activeSequence: number
  /** Saves or updates the current chat draft (called by ChatWindow on unmount/nav/change) */
  preserveDraft: (state: Omit<ChatDraftContextState, 'savedAt' | 'sequence'>) => void
  /** Explicitly clears the preserved draft and revokes any associated blob URLs */
  clearPreservedDraft: () => void
  /** Stages a pending import payload (for issue #2751) */
  stagePendingImport: (importPayload: Omit<PendingImportPayload, 'id' | 'sequence' | 'stagedAt'>) => PendingImportPayload
  /** Consumes the pending import payload if valid and not stale */
  consumePendingImport: (id?: string, currentAttackId?: string | null, currentConversationId?: string | null) => PendingImportPayload | null
  /** Dismisses or cancels a pending import payload, cleaning up any unapplied blob URLs */
  dismissPendingImport: (id?: string) => void
  /** Consumes the preserved draft for the matching destination, marking it consumed */
  consumePreservedDraft: (currentAttackId: string | null, currentConversationId?: string | null) => ChatDraftContent | null
  /** Checks whether a given draft content has dirty (non-empty) user data */
  isDraftDirty: (draft?: Partial<ChatDraftContent> | null) => boolean
}

/** Explicitly revokes all blob URLs associated with the given attachments. */
export function revokeAttachmentUrls(attachments?: readonly MessageAttachment[]): void {
  if (!attachments || typeof URL === 'undefined' || typeof URL.revokeObjectURL !== 'function') return
  for (const attachment of attachments) {
    if (attachment?.url && attachment.url.startsWith('blob:')) {
      try {
        URL.revokeObjectURL(attachment.url)
      } catch {
        // ignore errors during cleanup
      }
    }
  }
}

/** Revokes blob URLs in previousAttachments that are not present in nextAttachments. */
export function releaseSupersededUrls(
  previousAttachments?: readonly MessageAttachment[],
  nextAttachments?: readonly MessageAttachment[],
): void {
  if (typeof URL === 'undefined' || typeof URL.revokeObjectURL !== 'function') return
  const activeUrls = new Set((nextAttachments ?? []).map((a) => a.url))
  for (const prev of previousAttachments ?? []) {
    if (prev?.url && prev.url.startsWith('blob:') && !activeUrls.has(prev.url)) {
      try {
        URL.revokeObjectURL(prev.url)
      } catch {
        // ignore errors during cleanup
      }
    }
  }
}

/** Determines if draft content contains user-entered text, attachments, system prompt, or converters. */
export function isChatDraftDirty(draft?: Partial<ChatDraftContent> | null): boolean {
  if (!draft) return false
  if (typeof draft.text === 'string' && draft.text.trim().length > 0) return true
  if (Array.isArray(draft.attachments) && draft.attachments.length > 0) return true
  if (typeof draft.systemPrompt === 'string' && draft.systemPrompt.trim().length > 0) return true
  if (draft.converters?.applied && Object.keys(draft.converters.applied).length > 0) return true
  return false
}

/**
 * Checks whether a preserved draft belongs to the destination attack and conversation.
 * Prevents an unrelated draft from overwriting another attack or conversation.
 */
export function shouldApplyPreservedDraft(
  preserved: ChatDraftContextState | null,
  attackId: string | null,
  conversationId?: string | null,
): boolean {
  if (!preserved || !isChatDraftDirty(preserved.draft)) return false

  // If preserved draft came from an unsaved chat, destination must be an unsaved chat (null)
  if (preserved.originatingAttackId === null) {
    return attackId === null
  }
  // If preserved draft came from a persisted attack, destination attackId must match
  if (attackId !== preserved.originatingAttackId) {
    return false
  }
  // If originatingConversationId was specified, it should match the destination conversation when known
  if (preserved.originatingConversationId && conversationId) {
    return preserved.originatingConversationId === conversationId
  }
  return true
}

/**
 * Checks whether a pending import can be applied to the current attack/conversation.
 */
export function canApplyPendingImport(
  pending?: PendingImportPayload | null,
  attackId?: string | null,
  conversationId?: string | null,
): boolean {
  if (!pending) return false
  if (pending.destinationType === 'any') {
    return true
  }
  if (pending.destinationType === 'new_attack') {
    return attackId === null
  }
  if (pending.destinationType === 'current_conversation') {
    if (attackId !== pending.targetAttackId) return false
    if (pending.targetConversationId && conversationId) {
      return pending.targetConversationId === conversationId
    }
    return true
  }
  if (pending.destinationType === 'new_conversation') {
    return attackId === pending.targetAttackId
  }
  return false
}
