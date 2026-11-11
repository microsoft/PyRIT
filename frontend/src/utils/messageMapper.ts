import type {
  AttachmentUploadOutcome,
  BackendMessage,
  BackendMessagePiece,
  BackendScore,
  DisplayScore,
  Message,
  MessageAttachment,
  MessageDisplayPiece,
  MessageError,
  MessagePieceRequest,
} from '@/types'
import { mediaApi } from '@/services/api'
import { toApiError } from '@/services/errors'

import { generationDisplay } from './requestGeneration'

// File identity survives draft copies and handoffs without retaining discarded files.
const ATTACHMENT_UPLOADS = new WeakMap<File, {
  promise: Promise<AttachmentUploadOutcome>
  expiresAt: number
  outcome?: AttachmentUploadOutcome
}>()
const ATTACHMENT_UPLOAD_LISTENERS = new WeakMap<File, Set<(outcome: AttachmentUploadOutcome | undefined) => void>>()
// Leave a one-hour buffer before the server's staging lease expires.
const ATTACHMENT_REFRESH_BUFFER_MS = 60 * 60 * 1000
const LEGACY_ATTACHMENT_REUSE_MS = 23 * 60 * 60 * 1000
const MAX_ACTIVE_UPLOADS = 3
let activeUploads = 0
const pendingUploads: Array<() => void> = []

function queueAttachmentUpload(task: () => Promise<AttachmentUploadOutcome>): Promise<AttachmentUploadOutcome> {
  return new Promise((resolve, reject) => {
    const start = (): void => {
      activeUploads += 1
      void task().then(resolve, reject).finally(() => {
        activeUploads -= 1
        pendingUploads.shift()?.()
      })
    }
    if (activeUploads < MAX_ACTIVE_UPLOADS) start()
    else pendingUploads.push(start)
  })
}

/** Start once on attachment; message preparation awaits this same upload. */
export function startAttachmentUpload(file: File, retry = false): Promise<AttachmentUploadOutcome> {
  const existing = ATTACHMENT_UPLOADS.get(file)
  if (existing && !retry && existing.expiresAt > Date.now()) return existing.promise
  let expiresAt = Number.POSITIVE_INFINITY
  const upload = queueAttachmentUpload(async (): Promise<AttachmentUploadOutcome> => {
    try {
      const response = await mediaApi.upload({
        data_type: mimeTypeToDataType(file.type),
        content: await fileToBase64(file),
        mime_type: file.type || 'application/octet-stream',
      })
      if (response.expires_at !== undefined && !Number.isFinite(response.expires_at)) {
        throw new Error('The upload returned an invalid staging expiration.')
      }
      expiresAt = response.expires_at === undefined
        ? Date.now() + LEGACY_ATTACHMENT_REUSE_MS
        : response.expires_at * 1000 - ATTACHMENT_REFRESH_BUFFER_MS
      return { status: 'uploaded', value: response.value }
    } catch (error: unknown) {
      // Background failures are values so they can be shown before Send is clicked.
      return { status: 'failed', detail: toApiError(error).detail }
    }
  })
  ATTACHMENT_UPLOADS.set(file, { promise: upload, expiresAt: Number.POSITIVE_INFINITY })
  for (const listener of ATTACHMENT_UPLOAD_LISTENERS.get(file) ?? []) listener(undefined)
  void upload.then((outcome: AttachmentUploadOutcome) => {
    const current = ATTACHMENT_UPLOADS.get(file)
    if (current?.promise !== upload) return
    current.outcome = outcome
    current.expiresAt = expiresAt
    for (const listener of ATTACHMENT_UPLOAD_LISTENERS.get(file) ?? []) listener(outcome)
  })
  return upload
}

/** Observe replacements and their outcomes, not just the first upload promise. */
export function subscribeAttachmentUpload(
  file: File, listener: (outcome: AttachmentUploadOutcome | undefined) => void,
): () => void {
  const listeners = ATTACHMENT_UPLOAD_LISTENERS.get(file) ?? new Set()
  listeners.add(listener)
  ATTACHMENT_UPLOAD_LISTENERS.set(file, listeners)
  void startAttachmentUpload(file)
  listener(ATTACHMENT_UPLOADS.get(file)?.outcome)
  return () => {
    listeners.delete(listener)
    if (!listeners.size) ATTACHMENT_UPLOAD_LISTENERS.delete(file)
  }
}

export async function uploadedFileValue(file: File): Promise<string> {
  const outcome = await startAttachmentUpload(file)
  if (outcome.status === 'failed') throw new Error(`Upload failed for ${file.name}: ${outcome.detail}`)
  return outcome.value
}

/**
 * Read a File or Blob and return its contents as a base64-encoded string (no data URI prefix).
 */
export function fileToBase64(file: Blob): Promise<string> {
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
export function mimeTypeToDataType(mimeType?: string): string {
  if (!mimeType) return 'binary_path'
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
 * Compute the decoded byte count of a base64-encoded string. Whitespace and
 * trailing `=` padding are stripped before applying the standard
 * `floor(n * 3 / 4)` formula.
 */
function decodedBase64ByteCount(value: string): number {
  const stripped = value.replace(/\s+/g, '').replace(/=+$/, '')
  return Math.floor((stripped.length * 3) / 4)
}

function scoreWithProvenance(
  score: BackendScore,
  {
    piece,
    pieceIndex,
    filename,
  }: {
    piece: BackendMessagePiece
    pieceIndex: number
    filename?: string
  },
): DisplayScore {
  const pieceType = piece.converted_value_data_type
  const sourceLabel = [`Piece ${pieceIndex + 1}`, pieceType, filename].filter(Boolean).join(' · ')

  return {
    ...score,
    pieceIndex,
    pieceType,
    sourceLabel,
  }
}

/**
 * Build a frontend MessageAttachment from a backend piece.
 *
 * When `source` is `'converted'` (the default), uses `converted_value*` fields.
 * When `source` is `'original'`, uses `original_value*` fields instead.
 *
 * The backend exposes media in two forms: the raw stored value
 * (`*_value`, which may be a file path, blob URL, base64 string, etc.) and a
 * client-fetchable URL (`*_value_url`, populated by the mapper as a
 * `/api/media?path=...` link or a SAS-signed blob URL). When `*_value_url` is
 * present we use it directly; otherwise we fall back to the legacy detection
 * logic so older shaped payloads still render.
 */
function pieceToAttachment(
  piece: BackendMessagePiece,
  source: 'converted' | 'original' = 'converted',
): MessageAttachment | null {
  const isOriginal = source === 'original'
  const dataType = isOriginal ? piece.original_value_data_type : piece.converted_value_data_type
  const value = isOriginal ? piece.original_value : piece.converted_value
  const valueUrl = isOriginal ? piece.original_value_url : piece.converted_value_url
  const mimeField = isOriginal ? piece.original_value_mime_type : piece.converted_value_mime_type

  if (!isMediaDataType(dataType)) return null

  const mediaValue = value || ''
  // No renderable media: produce no attachment at all. Any scores on the piece
  // are surfaced by the media display piece instead, so score-only pieces never
  // leak into copy / download / export paths.
  if (!valueUrl && !mediaValue) return null

  const mime = mimeField || defaultMimeForDataType(dataType)
  // Detect base64-encoded content while excluding file paths and URL schemes.
  // Base64 charset includes '/' so naive regex would match relative paths.
  const looksLikePathOrScheme = /^[A-Za-z]:\\/.test(mediaValue) || // Windows path
    mediaValue.startsWith('/') ||                                   // Unix absolute path
    /^[a-z][a-z0-9+.-]*:/i.test(mediaValue)                        // URI scheme (file:, blob:, etc.)
  const isBase64 = !looksLikePathOrScheme &&
    mediaValue.length >= 16 && /^[A-Za-z0-9+/=\n]+$/.test(mediaValue)
  // Prefer the mapper-resolved URL when present; fall back to existing logic
  // (base64 inline data URI or raw value-as-URL) for compatibility.
  const url = valueUrl || (isBase64 ? buildDataUri(mediaValue, mime) : mediaValue)
  const prefix = isOriginal ? 'original_' : ''
  const filename = isOriginal ? piece.original_filename : piece.converted_filename
  const fallbackName = `${prefix}${dataType}_${piece.id.slice(0, 8)}`

  // For base64-inlined media, derive the decoded byte count. For path / URL
  // values the string length is meaningless (e.g. /api/media?path=... is a
  // reference, not the payload), so size is omitted and the UI must hide it.
  const size = isBase64 && !valueUrl ? decodedBase64ByteCount(mediaValue) : undefined

  return {
    type: dataTypeToAttachmentType(dataType),
    name: filename || fallbackName,
    url,
    mimeType: mime,
    size,
    sourceValue: mediaValue,
    sourceDataType: isOriginal ? dataType : undefined,
    pieceId: piece.id,
    metadata: piece.prompt_metadata || undefined,
  }
}

/**
 * Rebuild editable input using only a persisted message's original values.
 */
export function backendMessageToOriginalDraft(
  msg: BackendMessage,
): Pick<Message, 'content' | 'attachments'> {
  const textParts: string[] = []
  const attachments: MessageAttachment[] = []

  for (const piece of msg.message_pieces) {
    if (piece.original_value && !isMediaDataType(piece.original_value_data_type)) {
      textParts.push(piece.original_value)
    }

    const attachment = pieceToAttachment(piece, 'original')
    if (attachment) {
      attachments.push(attachment)
    }
  }

  return {
    content: textParts.join('\n'),
    attachments: attachments.length > 0 ? attachments : undefined,
  }
}

/**
 * Build the display name used to label a media piece's scores, matching the
 * attachment name so score provenance reads the same with or without media.
 */
function mediaPieceScoreFilename(piece: BackendMessagePiece): string {
  return piece.converted_filename || `${piece.converted_value_data_type}_${piece.id.slice(0, 8)}`
}

/**
 * Extract an error from a backend message piece, if any.
 */
function pieceToError(piece: BackendMessagePiece): MessageError | undefined {
  if (piece.response_error && piece.response_error !== 'none') {
    const fallbackDescriptions: Record<string, string> = {
      blocked: 'The target blocked this message.',
      processing: 'The target could not process this message.',
      empty: 'The target returned an empty response.',
      unknown: 'The target returned an unknown error.',
    }
    return {
      type: piece.response_error,
      description: piece.response_error === 'processing'
        ? piece.converted_value || piece.original_value || piece.response_error_description || fallbackDescriptions.processing
        : piece.response_error_description || fallbackDescriptions[piece.response_error],
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
  const displayPieces: MessageDisplayPiece[] = []
  const reasoningSummaries: string[] = []
  let error: MessageError | undefined

  for (const [pieceIndex, piece] of msg.message_pieces.entries()) {
    // Check for errors
    const pieceError = pieceToError(piece)
    if (pieceError && !error) {
      error = pieceError
    }
    // Error details belong in the error panel, not in editable message content.
    const isProcessingError = pieceError?.type === 'processing'
    if (isProcessingError || isReasoningDataType(piece.converted_value_data_type)) {
      if (!isProcessingError) {
        reasoningSummaries.push(...extractReasoningSummaries(piece.converted_value))
      }
      const scores = piece.scores
        .map((score) => scoreWithProvenance(score, { piece, pieceIndex }))
        .sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime())
      if (scores.length > 0) {
        displayPieces.push({
          type: 'text',
          pieceId: piece.id,
          pieceIndex,
          content: '',
          scores,
        })
      }
      continue
    }

    // Extract text content from text-type pieces (converted)
    if (!isMediaDataType(piece.converted_value_data_type)) {
      if (piece.converted_value) {
        textParts.push(piece.converted_value)
      }
      const scores = piece.scores
        .map((score) => scoreWithProvenance(score, { piece, pieceIndex }))
        .sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime())
      if (piece.converted_value || scores.length > 0) {
        displayPieces.push({
          type: 'text',
          pieceId: piece.id,
          pieceIndex,
          content: piece.converted_value,
          scores: scores.length > 0 ? scores : undefined,
        })
      }
    }

    // Extract original text content
    if (piece.original_value && !isMediaDataType(piece.original_value_data_type)) {
      originalTextParts.push(piece.original_value)
    }

    // Extract media attachments (converted). Scores live on the display piece
    // so a piece with scores but no renderable media still shows them without
    // fabricating an attachment.
    if (isMediaDataType(piece.converted_value_data_type)) {
      const att = pieceToAttachment(piece)
      const mediaScores = piece.scores
        .map((score) =>
          scoreWithProvenance(score, { piece, pieceIndex, filename: mediaPieceScoreFilename(piece) }),
        )
        .sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime())

      if (att) {
        attachments.push(att)
      }
      if (att || mediaScores.length > 0) {
        displayPieces.push({
          type: 'media',
          pieceId: piece.id,
          pieceIndex,
          attachment: att || undefined,
          scores: mediaScores.length > 0 ? mediaScores : undefined,
        })
      }
    }

    // Extract original media attachments
    const origAtt = pieceToAttachment(piece, 'original')
    if (origAtt) {
      originalAttachments.push(origAtt)
    }
  }

  const role = ['simulated_assistant', 'assistant', 'tool', 'simulated_tool', 'system', 'developer'].includes(msg.role)
    ? msg.role
    : 'user'

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
    ...(role === 'user' || role === 'assistant' ? generationDisplay(msg.message_pieces[0]?.prompt_metadata) : {}),
    attachments: attachments.length > 0 ? attachments : undefined,
    displayPieces: displayPieces.length > 0 ? displayPieces : undefined,
    error,
    reasoningSummaries: reasoningSummaries.length > 0 ? reasoningSummaries : undefined,
    originalContent: hasTextDiff ? originalContent : undefined,
    originalAttachments: hasMediaDiff ? originalAttachments : undefined,
  }
}

/**
 * Merge saved piece pages without duplicating or splitting their conversation turns.
 */
export function mergeBackendMessages(previous: BackendMessage[], incoming: BackendMessage[]): BackendMessage[] {
  const turns = new Map<number, { message: BackendMessage; pieces: Map<string, BackendMessagePiece> }>()
  const pieceOwners = new Map<string, number>()
  for (const message of [...previous, ...incoming]) {
    let turn = turns.get(message.turn_number)
    if (turn && turn.message.role !== message.role) {
      throw new Error(`Saved turn ${message.turn_number} changed role. Restart transcript loading.`)
    }
    if (!turn) {
      turn = { message, pieces: new Map() }
      turns.set(message.turn_number, turn)
    }
    for (const piece of message.message_pieces) {
      const owner = pieceOwners.get(piece.id)
      if (owner !== undefined && owner !== message.turn_number) {
        throw new Error('A saved piece changed turns. Restart transcript loading.')
      }
      pieceOwners.set(piece.id, message.turn_number)
      const existing = turn.pieces.get(piece.id)
      const scores = new Map<string, BackendScore>()
      for (const score of [...(existing?.scores ?? []), ...piece.scores]) {
        scores.set(score.id, score)
      }
      turn.pieces.set(piece.id, { ...existing, ...piece, scores: [...scores.values()] })
    }
  }
  return [...turns.values()].sort((a, b) => a.message.turn_number - b.message.turn_number).map(({ message, pieces }) => {
    const orderedPieces = [...pieces.values()]
    if (orderedPieces.every((piece) => piece.timestamp !== undefined)) {
      orderedPieces.sort((a: BackendMessagePiece, b: BackendMessagePiece): number => {
        const firstTimestamp = a.timestamp ?? ''
        const secondTimestamp = b.timestamp ?? ''
        const millisecondOrder = Date.parse(firstTimestamp) - Date.parse(secondTimestamp)
        if (millisecondOrder) return millisecondOrder
        // Date truncates Python's microseconds; keep them ahead of the ID tie-break.
        const firstFraction = firstTimestamp.match(/\.(\d+)/)?.[1] ?? ''
        const secondFraction = secondTimestamp.match(/\.(\d+)/)?.[1] ?? ''
        const precision = Math.max(firstFraction.length, secondFraction.length)
        return firstFraction.padEnd(precision, '0').localeCompare(secondFraction.padEnd(precision, '0'))
          || a.id.localeCompare(b.id)
      })
    }
    return { ...message, created_at: orderedPieces[0]?.timestamp ?? message.created_at, message_pieces: orderedPieces }
  })
}

/**
 * Convert all backend messages to frontend messages.
 */
export function backendMessagesToFrontend(messages: BackendMessage[], partialSequences?: readonly number[]): Message[] {
  return messages.map((message) => ({
    ...backendMessageToFrontend(message),
    ...(partialSequences === undefined ? {} : {
      sequence: message.turn_number,
      isPartial: partialSequences.includes(message.turn_number),
    }),
  }))
}

/**
 * Convert a frontend MessageAttachment (with File) to a backend MessagePieceRequest.
 */
export async function attachmentToMessagePieceRequest(att: MessageAttachment): Promise<MessagePieceRequest> {
  let base64Value: string
  if (att.file) {
    if (ATTACHMENT_UPLOADS.has(att.file)) {
      base64Value = await uploadedFileValue(att.file)
    } else {
      base64Value = await fileToBase64(att.file)
    }
  } else if (att.sourceValue != null) {
    base64Value = att.sourceValue
  } else if (att.url.startsWith('data:')) {
    base64Value = att.url.split(',')[1] || ''
  } else {
    base64Value = att.url
  }

  return {
    data_type: att.sourceDataType ?? mimeTypeToDataType(att.mimeType),
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
  attachments: MessageAttachment[],
  textMetadata?: Record<string, unknown>,
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
      prompt_metadata: videoId ? { ...textMetadata, video_id: videoId } : textMetadata,
    })
  }

  // Add attachment pieces
  for (const att of attachments) {
    pieces.push(await attachmentToMessagePieceRequest(att))
  }

  return pieces
}
