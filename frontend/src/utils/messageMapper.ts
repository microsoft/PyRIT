import type {
  BackendMessage,
  BackendMessagePiece,
  Message,
  MessageAttachment,
  MessageError,
  MessagePieceRequest,
} from '../types'

/**
 * Read a File and return its contents as a base64-encoded string (no data URI prefix).
 */
export function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = reader.result as string
      // Strip the data:...;base64, prefix
      const base64 = result.split(',')[1] || ''
      resolve(base64)
    }
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}

/**
 * Map a frontend MIME type to the backend PromptDataType convention.
 */
export function mimeTypeToDataType(mimeType: string): string {
  if (mimeType.startsWith('image/')) return 'image_path'
  if (mimeType.startsWith('audio/')) return 'audio_path'
  if (mimeType.startsWith('video/')) return 'video_path'
  return 'binary_path'
}

/**
 * Map a backend `converted_value_data_type` to a frontend attachment type.
 */
export function dataTypeToAttachmentType(dataType: string): 'image' | 'audio' | 'video' | 'file' {
  if (dataType.includes('image')) return 'image'
  if (dataType.includes('audio')) return 'audio'
  if (dataType.includes('video')) return 'video'
  return 'file'
}

/**
 * Build a data URI from base64 content and a MIME type.
 */
export function buildDataUri(base64Value: string, mimeType: string): string {
  return `data:${mimeType};base64,${base64Value}`
}

/**
 * Determine a default MIME type for a backend data type when none is provided.
 */
function defaultMimeForDataType(dataType: string): string {
  if (dataType.includes('image')) return 'image/png'
  if (dataType.includes('audio')) return 'audio/wav'
  if (dataType.includes('video')) return 'video/mp4'
  return 'application/octet-stream'
}

/**
 * Check if a backend data type represents non-text media content.
 */
function isMediaDataType(dataType: string): boolean {
  return dataType.includes('image') || dataType.includes('audio') || dataType.includes('video') || dataType.includes('binary')
}

/**
 * Check if a backend data type represents reasoning/thinking content.
 */
function isReasoningDataType(dataType: string): boolean {
  return dataType === 'reasoning'
}

/**
 * Extract summary texts from a reasoning piece's value.
 * The value is JSON like: {"type": "reasoning", "summary": [{"type": "summary_text", "text": "..."}]}
 * Falls back to displaying content or a placeholder when no summaries are available.
 */
function extractReasoningSummaries(value: string): string[] {
  try {
    const parsed = JSON.parse(value)
    if (parsed?.summary && Array.isArray(parsed.summary)) {
      const texts = parsed.summary
        .filter((s: { type?: string; text?: string }) => s.text)
        .map((s: { text: string }) => s.text)
      if (texts.length > 0) return texts
    }
    // If summaries are empty but there's readable content, show that
    if (typeof parsed?.content === 'string' && parsed.content.trim()) {
      return [parsed.content]
    }
    // Reasoning occurred but content is encrypted or empty
    if (parsed?.type === 'reasoning') {
      return ['(Reasoning was performed but details are not available)']
    }
  } catch {
    // If not valid JSON, use the raw value if non-empty
    if (value.trim()) return [value]
  }
  return []
}

/**
 * Build a frontend MessageAttachment from a backend piece.
 *
 * When `source` is `'converted'` (the default), uses `converted_value*` fields.
 * When `source` is `'original'`, uses `original_value*` fields instead.
 */
function pieceToAttachment(
  piece: BackendMessagePiece,
  source: 'converted' | 'original' = 'converted',
): MessageAttachment | null {
  const isOriginal = source === 'original'
  const dataType = isOriginal ? piece.original_value_data_type : piece.converted_value_data_type
  const value = isOriginal ? piece.original_value : piece.converted_value
  const mimeField = isOriginal ? piece.original_value_mime_type : piece.converted_value_mime_type

  if (!isMediaDataType(dataType) || !value) return null

  const mime = mimeField || defaultMimeForDataType(dataType)
  // Detect base64-encoded content while excluding file paths and URL schemes.
  // Base64 charset includes '/' so naive regex would match relative paths.
  const looksLikePathOrScheme = /^[A-Za-z]:\\/.test(value) || // Windows path
    value.startsWith('/') ||                                   // Unix absolute path
    /^[a-z][a-z0-9+.-]*:/i.test(value)                        // URI scheme (file:, blob:, etc.)
  const isBase64 = !looksLikePathOrScheme &&
    value.length >= 16 && /^[A-Za-z0-9+/=\n]+$/.test(value)
  const url = isBase64 ? buildDataUri(value, mime) : value
  const prefix = isOriginal ? 'original_' : ''
  const filename = isOriginal ? piece.original_filename : piece.converted_filename
  const fallbackName = `${prefix}${dataType}_${piece.piece_id.slice(0, 8)}`

  return {
    type: dataTypeToAttachmentType(dataType),
    name: filename || fallbackName,
    url,
    mimeType: mime,
    size: value.length,
    pieceId: piece.piece_id,
    metadata: piece.prompt_metadata || undefined,
  }
}

/**
 * Extract an error from a backend message piece, if any.
 */
function pieceToError(piece: BackendMessagePiece): MessageError | undefined {
  if (piece.response_error && piece.response_error !== 'none') {
    return {
      type: piece.response_error,
      description: piece.response_error_description || undefined,
    }
  }
  return undefined
}

/**
 * Convert a single backend Message DTO to a frontend Message for rendering.
 */
export function backendMessageToFrontend(msg: BackendMessage): Message {
  const textParts: string[] = []
  const originalTextParts: string[] = []
  const attachments: MessageAttachment[] = []
  const originalAttachments: MessageAttachment[] = []
  const reasoningSummaries: string[] = []
  let error: MessageError | undefined

  for (const piece of msg.pieces) {
    // Check for errors
    const pieceError = pieceToError(piece)
    if (pieceError && !error) {
      error = pieceError
    }

    // Extract reasoning summaries from reasoning-type pieces
    if (isReasoningDataType(piece.converted_value_data_type)) {
      const summaries = extractReasoningSummaries(piece.converted_value)
      reasoningSummaries.push(...summaries)
      continue
    }

    // Extract text content from text-type pieces (converted)
    if (!isMediaDataType(piece.converted_value_data_type)) {
      if (piece.converted_value) {
        textParts.push(piece.converted_value)
      }
    }

    // Extract original text content
    if (piece.original_value && !isMediaDataType(piece.original_value_data_type)) {
      originalTextParts.push(piece.original_value)
    }

    // Extract media attachments (converted)
    const att = pieceToAttachment(piece)
    if (att) {
      attachments.push(att)
    }

    // Extract original media attachments
    const origAtt = pieceToAttachment(piece, 'original')
    if (origAtt) {
      originalAttachments.push(origAtt)
    }
  }

  const role = ['simulated_assistant', 'assistant', 'system'].includes(msg.role)
    ? msg.role
    : msg.role === 'developer' ? 'system' : 'user'

  const convertedContent = textParts.join('\n')
  const originalContent = originalTextParts.join('\n')

  // Only include originalContent when it actually differs from converted
  const hasTextDiff = originalContent !== '' && originalContent !== convertedContent
  const hasMediaDiff = originalAttachments.length > 0 &&
    JSON.stringify(originalAttachments.map(a => a.url)) !== JSON.stringify(attachments.map(a => a.url))

  return {
    role: role as Message['role'],
    content: convertedContent,
    timestamp: msg.created_at,
    attachments: attachments.length > 0 ? attachments : undefined,
    error,
    reasoningSummaries: reasoningSummaries.length > 0 ? reasoningSummaries : undefined,
    originalContent: hasTextDiff ? originalContent : undefined,
    originalAttachments: hasMediaDiff ? originalAttachments : undefined,
  }
}

/**
 * Convert all backend messages to frontend messages.
 */
export function backendMessagesToFrontend(messages: BackendMessage[]): Message[] {
  return messages.map(backendMessageToFrontend)
}

/**
 * Convert a frontend MessageAttachment (with File) to a backend MessagePieceRequest.
 */
export async function attachmentToMessagePieceRequest(att: MessageAttachment): Promise<MessagePieceRequest> {
  let base64Value = ''
  if (att.file) {
    base64Value = await fileToBase64(att.file)
  } else if (att.url.startsWith('data:')) {
    base64Value = att.url.split(',')[1] || ''
  } else {
    base64Value = att.url
  }

  return {
    data_type: mimeTypeToDataType(att.mimeType),
    original_value: base64Value,
    mime_type: att.mimeType,
    original_prompt_id: att.pieceId,
    prompt_metadata: att.metadata,
  }
}

/**
 * Build the pieces array for an AddMessageRequest from text + attachments.
 */
export async function buildMessagePieces(
  text: string,
  attachments: MessageAttachment[]
): Promise<MessagePieceRequest[]> {
  const pieces: MessagePieceRequest[] = []

  // Check for video_id in video attachments (needed for remix mode)
  const videoId = attachments
    .filter(a => a.type === 'video')
    .map(a => a.metadata?.video_id)
    .find(id => id != null)

  // Add text piece if present
  if (text.trim()) {
    pieces.push({
      data_type: 'text',
      original_value: text,
      prompt_metadata: videoId ? { video_id: videoId } : undefined,
    })
  }

  // Add attachment pieces
  for (const att of attachments) {
    pieces.push(await attachmentToMessagePieceRequest(att))
  }

  return pieces
}
