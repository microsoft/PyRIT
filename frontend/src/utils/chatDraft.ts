import type {
  ChatDraftContent, ChatDraftContextState, ConverterPipelineStage, MessageAttachment, PendingImportPayload,
} from '@/types'

export type { ChatDraftContent, ChatDraftContextState, ChatDraftHandoffContextValue, PendingImportPayload } from '@/types'

/** Explicitly revokes all blob URLs associated with the given attachments. */
export function revokeAttachmentUrls(attachments?: readonly MessageAttachment[]): void {
  if (!attachments || typeof URL === 'undefined' || typeof URL.revokeObjectURL !== 'function') return
  for (const url of new Set(attachments.map((attachment: MessageAttachment) => attachment.url))) {
    if (url.startsWith('blob:')) URL.revokeObjectURL(url)
  }
}

/** Revokes blob URLs in previousAttachments that are not present in nextAttachments. */
export function releaseSupersededUrls(
  previousAttachments?: readonly MessageAttachment[],
  nextAttachments?: readonly MessageAttachment[],
): void {
  if (typeof URL === 'undefined' || typeof URL.revokeObjectURL !== 'function') return
  const activeUrls = new Set((nextAttachments ?? []).map((attachment: MessageAttachment) => attachment.url))
  revokeAttachmentUrls(previousAttachments?.filter((attachment: MessageAttachment) => !activeUrls.has(attachment.url)))
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

/** Setup must survive navigation without counting as prompt replacement. */
export function hasChatDraftState(draft?: Partial<ChatDraftContent> | null): boolean {
  return Boolean(draft && (
    isChatDraftDirty(draft)
    || draft.target !== undefined
    || Object.values(draft.converters?.pipelines ?? {}).some((stages: ConverterPipelineStage[]) => stages.length > 0)
  ))
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
  return Boolean(preserved && hasChatDraftState(preserved.draft)
    && attackId === preserved.originatingAttackId
    && (conversationId ?? null) === preserved.originatingConversationId)
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
    return attackId === null && conversationId == null
  }
  return Boolean(pending.targetAttackId && pending.targetConversationId
    && attackId === pending.targetAttackId && conversationId === pending.targetConversationId)
}
