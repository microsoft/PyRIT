import type {
  BackendMessage, ConversationDraftMessage, ConversationDraftPiece, ConversationDraftRole,
  ConverterInputPiece, MessageAttachment, SaveConversationRequest, TargetInstance,
} from '@/types'
import { generateClientId } from './clientId'
import { dataTypeToAttachmentType, fileToBase64, mimeTypeToDataType } from './messageMapper'
import { buildMediaUrl, isPathDataType } from './media'

export const DRAFT_ROLES: ConversationDraftRole[] = ['system', 'user', 'simulated_assistant', 'tool', 'developer']
export const NEW_MESSAGE_ROLES: ConversationDraftRole[] = ['system', 'developer', 'user', 'simulated_assistant']
const TOOL_DATA_TYPES = ['function_call', 'function_call_output', 'tool_call']

export function draftToolTypes(messages: ConversationDraftMessage[]): string[] {
  return [...new Set(messages.flatMap((message: ConversationDraftMessage) => message.pieces
    .map((piece: ConversationDraftPiece) => piece.converted_value_data_type ?? piece.data_type)
    .filter((type: string) => TOOL_DATA_TYPES.includes(type))))]
}

export function editorTargetDisabledReason(target: TargetInstance, toolTypes: string[]): string | undefined {
  if (!target.capabilities?.supports_editable_history || !target.capabilities.supports_multi_turn) {
    return 'This target does not support editable history.'
  }
  const unsupported = toolTypes.filter((type: string) => !target.capabilities?.supported_input_modalities.includes(type))
  if (unsupported.length) return `This target does not support these tool pieces: ${unsupported.join(', ')}.`
  return undefined
}

export function unansweredToolCallId(messages: ConversationDraftMessage[], messageId: string): string | undefined {
  const responses = new Set<string>()
  const calls: string[] = []
  for (const message of messages) {
    for (const piece of message.pieces) {
      const type = piece.converted_value_data_type ?? piece.data_type
      if (!['function_call', 'function_call_output'].includes(type)) continue
      let value: unknown
      try { value = JSON.parse(piece.converted_value ?? piece.original_value) }
      catch { continue } // Incomplete payloads remain visible with validateDraft's error.
      if (!value || typeof value !== 'object' || Array.isArray(value)) continue
      const data = value as Record<string, unknown>
      const id = data.call_id ?? data.id
      if (typeof id !== 'string' || !id.trim()) continue
      if (type === 'function_call_output') responses.add(id)
      else if (message.id === messageId) calls.push(id)
    }
  }
  return calls.find((id: string) => !responses.has(id))
}

export function newDraftPiece(dataType = 'text'): ConversationDraftPiece {
  return { draftId: generateClientId(), data_type: dataType, original_value: '' }
}

export function newDraftMessage(): ConversationDraftMessage {
  return { id: generateClientId(), role: 'user', pieces: [newDraftPiece()] }
}

export function validateDraft(messages: ConversationDraftMessage[]): string | null {
  const calls = new Set<string>()
  const responses = new Set<string>()
  for (const [index, message] of messages.entries()) {
    if (!message.pieces.length) return `Message ${index + 1} needs content. Add a piece or delete the message.`
    for (const piece of message.pieces) {
      const type = piece.converted_value_data_type ?? piece.data_type
      if (!['function_call', 'function_call_output', 'tool_call'].includes(type)) continue
      const prefix = `Message ${index + 1}: `
      try {
        const value: unknown = JSON.parse(piece.converted_value ?? piece.original_value)
        if (!value || typeof value !== 'object' || Array.isArray(value)) return `${prefix}Tool content must be a JSON object.`
        const data = value as Record<string, unknown>
        if (type === 'function_call_output') {
          if (message.role !== 'tool') return `${prefix}A tool response needs the tool role.`
          if (typeof data.call_id !== 'string' || !calls.has(data.call_id) || responses.has(data.call_id)) {
            return `${prefix}A tool response needs one preceding, unanswered call with the same call ID.`
          }
          if (!('output' in data)) return `${prefix}A tool response needs an output.`
          responses.add(data.call_id)
        } else {
          if (message.role !== 'simulated_assistant') return `${prefix}A tool call needs the simulated_assistant role.`
          if (type === 'tool_call') continue
          const id = data.call_id ?? data.id
          const functionData = data.function && typeof data.function === 'object'
            ? data.function as Record<string, unknown> : data
          if (typeof id !== 'string' || !id.trim() || typeof functionData.name !== 'string'
            || !functionData.name.trim() || typeof functionData.arguments !== 'string') {
            return `${prefix}A tool call needs a call ID, a name, and JSON arguments.`
          }
          const args: unknown = JSON.parse(functionData.arguments)
          if (!args || typeof args !== 'object' || Array.isArray(args)) return `${prefix}Arguments must be a JSON object.`
          if (calls.has(id)) return `${prefix}Duplicate tool call ID: ${id}`
          calls.add(id)
        }
      } catch {
        return `${prefix}Tool content and arguments must be valid JSON.`
      }
    }
  }
  return null
}

export function toConversationDraft(messages: BackendMessage[]): ConversationDraftMessage[] {
  return messages.map((message: BackendMessage): ConversationDraftMessage => {
    const role = message.role === 'assistant' ? 'simulated_assistant' : message.role
    if (!DRAFT_ROLES.some((candidate: string) => candidate === role)) {
      throw new Error(`Cannot edit unsupported role: ${role}`)
    }
    return {
      id: generateClientId(),
      role: role as ConversationDraftRole,
      pieces: message.message_pieces.map((piece): ConversationDraftPiece => ({
        draftId: generateClientId(),
        source_piece_id: piece.id,
        data_type: piece.original_value_data_type,
        original_value: piece.original_value ?? '',
        converted_value: piece.converted_value,
        converted_value_data_type: piece.converted_value_data_type,
        mime_type: piece.original_value_mime_type ?? undefined,
        prompt_metadata: piece.prompt_metadata ? { ...piece.prompt_metadata } : undefined,
        previewUrl: piece.original_value_url ?? undefined,
        filename: piece.original_filename ?? undefined,
      })),
    }
  })
}

export function draftAttachment(piece: ConversationDraftPiece): MessageAttachment {
  return {
    draftId: piece.draftId,
    type: dataTypeToAttachmentType(piece.data_type),
    name: piece.filename ?? piece.data_type,
    url: piece.previewUrl ?? buildMediaUrl(piece.original_value),
    mimeType: piece.mime_type ?? 'application/octet-stream',
    file: piece.file,
    sourceValue: piece.file ? undefined : piece.original_value,
    sourceDataType: piece.data_type,
  }
}

export function attachmentDraft(attachment: MessageAttachment): ConversationDraftPiece {
  return {
    draftId: attachment.draftId ?? generateClientId(),
    data_type: attachment.sourceDataType ?? mimeTypeToDataType(attachment.mimeType),
    original_value: attachment.sourceValue ?? '',
    mime_type: attachment.mimeType,
    previewUrl: attachment.url,
    filename: attachment.name,
    file: attachment.file,
  }
}

export function draftConverterInputs(messages: ConversationDraftMessage[], selected: Set<string>): ConverterInputPiece[] {
  return messages.flatMap((message: ConversationDraftMessage, index: number) => selected.has(message.id)
    ? message.pieces.filter((piece: ConversationDraftPiece) => piece.data_type === 'text' || isPathDataType(piece.data_type))
      .map((piece: ConversationDraftPiece): ConverterInputPiece => ({
        id: piece.draftId,
        name: `Message ${index + 1}: ${piece.filename ?? piece.data_type}`,
        pieceType: piece.data_type === 'text' ? 'text' : dataTypeToAttachmentType(piece.data_type),
        dataType: piece.data_type,
        value: piece.original_value,
        file: piece.file,
      }))
    : [])
}

export async function serializeDraft(messages: ConversationDraftMessage[]): Promise<SaveConversationRequest['messages']> {
  return Promise.all(messages.map(async (message: ConversationDraftMessage) => ({
    role: message.role,
    pieces: await Promise.all(message.pieces.map(async (piece: ConversationDraftPiece) => ({
      data_type: piece.data_type,
      original_value: piece.file ? await fileToBase64(piece.file) : piece.original_value,
      converted_value: piece.converted_value,
      converted_value_data_type: piece.converted_value_data_type,
      applied_converter_ids: piece.converted_value === undefined ? undefined : piece.applied_converter_ids,
      source_piece_id: piece.source_piece_id,
      mime_type: piece.mime_type,
      prompt_metadata: piece.prompt_metadata,
    }))),
  })))
}
