import type { Message, MessageAttachment, MessageError } from '../types'
import { fileToBase64 } from './messageMapper'

export type ExportFormat = 'markdown' | 'json' | 'html'

const FILE_EXTENSIONS: Record<ExportFormat, string> = {
  markdown: 'md',
  json: 'json',
  html: 'html',
}

export const EXPORT_MIME_TYPES: Record<ExportFormat, string> = {
  markdown: 'text/markdown;charset=utf-8',
  json: 'application/json;charset=utf-8',
  html: 'text/html;charset=utf-8',
}

const ROLE_LABELS: Record<Message['role'], string> = {
  user: 'User',
  assistant: 'Assistant',
  simulated_assistant: 'Simulated Assistant',
  system: 'System',
}

/** Friendly label per attachment type, matching the API's media preview wording. */
const MEDIA_LABELS: Record<MessageAttachment['type'], string> = {
  image: 'Image',
  audio: 'Audio',
  video: 'Video',
  file: 'File',
}

/** How each omission reads to whoever opens the file. */
const OMISSION_REASONS: Record<OmissionReason, string> = {
  unreadable: 'could not be read',
  remote: 'kept in remote storage',
  'too-large': 'too large to embed',
  'no-room': 'no room left in this file',
  'not-embeddable': 'not a media file',
}

/** Fixed order so the summary reads the same way for the same conversation. */
const OMISSION_ORDER: OmissionReason[] = [
  'unreadable',
  'remote',
  'too-large',
  'no-room',
  'not-embeddable',
]

/**
 * Largest attachment inlined into an HTML export, measured in real bytes.
 * Base64 inflates bytes by a third, so anything above this is listed by name
 * instead of embedded to keep the file openable.
 */
const MAX_INLINE_ATTACHMENT_BYTES = 10 * 1024 * 1024

/**
 * Largest total media text one HTML export may carry, counted in the characters
 * actually written for it. This is a ceiling against runaway growth rather than
 * a size to aim for: because base64 costs a third on top, 50 MiB of text holds
 * roughly 37 MiB of real media. An ordinary conversation uses a fraction of
 * that, one carrying a dozen full-size images can approach it, and a file at
 * the ceiling still opens in a few seconds. It is not a promise the file will
 * fit an email. Surrounding markup is not counted, so this bounds the file
 * closely rather than exactly.
 *
 * Reaching it does not stop the walk: a later attachment that still fits is
 * embedded, so an omission can sit above media that made it in.
 */
export const MAX_TOTAL_INLINE_CHARACTERS = 50 * 1024 * 1024

/**
 * The only path the export reads attachment bytes from. Any other same-origin
 * path is answered by the single-page app, whose HTML would otherwise be
 * embedded as if it were the media.
 */
const MEDIA_ENDPOINT_PATH = '/api/media'

const HTML_STYLESHEET = `
body { font-family: 'Segoe UI', system-ui, sans-serif; color: #201f1e; margin: 2rem auto; max-width: 50rem; }
h1 { font-size: 1.5rem; }
.meta { color: #605e5c; font-size: 0.85rem; }
.message { border: 1px solid #d2d0ce; border-radius: 6px; margin: 1rem 0; padding: 0.75rem 1rem; }
.role { font-weight: 600; }
.timestamp { color: #605e5c; font-size: 0.85rem; margin-left: 0.5rem; }
.label { font-weight: 600; margin-bottom: 0.25rem; }
.placeholder { color: #605e5c; font-style: italic; }
.omissions { color: #605e5c; }
.error { color: #a4262c; }
pre { background: #f3f2f1; border-radius: 4px; padding: 0.5rem; white-space: pre-wrap; word-break: break-word; }
figure { margin: 0.5rem 0; }
figcaption { color: #605e5c; font-size: 0.85rem; }
img, video { height: auto; max-width: 100%; }
@media print {
  body { margin: 0; max-width: none; }
  .message { border-color: #8a8886; break-inside: avoid; page-break-inside: avoid; }
  @page { margin: 1.5cm; }
}
`

/**
 * Serialize, name, and download the currently viewed conversation in one call.
 * All formats share a single timestamp so the filename and the document body
 * agree.
 *
 * Only the HTML branch awaits: it reads attachment bytes back so it can embed
 * them. Markdown and JSON must stay free of `await` so they still download
 * synchronously.
 */
export async function exportConversation({
  messages,
  conversationId,
  format,
  now = new Date(),
}: {
  messages: Message[]
  conversationId: string | null
  format: ExportFormat
  now?: Date
}): Promise<void> {
  const content =
    format === 'html'
      ? await conversationToHtml(messages, conversationId, now)
      : format === 'markdown'
        ? conversationToMarkdown(messages, conversationId, now)
        : conversationToJson(messages, conversationId, now)
  downloadTextFile(content, buildExportFilename(conversationId, format, now), EXPORT_MIME_TYPES[format])
}

/**
 * Render the conversation as a human-readable Markdown transcript. Includes the
 * system message (hidden in the chat view) and drops the "typing" placeholder.
 * Free text is wrapped in dynamically sized code fences, and inline metadata
 * (attachment names, error text) has its newlines collapsed, so untrusted
 * content cannot corrupt the document structure.
 */
export function conversationToMarkdown(
  messages: Message[],
  conversationId: string | null,
  exportedAt: Date = new Date(),
): string {
  const exported = withoutLoadingPlaceholders(messages)
  const lines: string[] = [
    '# CoPyRIT conversation export',
    '',
    `- Conversation: ${inlineText(conversationId ?? '(unsaved)')}`,
    `- Exported: ${exportedAt.toISOString()}`,
    `- Messages: ${exported.length}`,
  ]

  for (const message of exported) {
    lines.push('', `## ${ROLE_LABELS[message.role]} — ${inlineText(message.timestamp)}`, '', fencedBlock(message.content))

    if (message.originalContent != null && message.originalContent !== message.content) {
      lines.push('', '**Original (before conversion):**', '', fencedBlock(message.originalContent))
    }
    appendAttachmentList(lines, 'Original attachments (before conversion):', message.originalAttachments)
    if (message.reasoningSummaries && message.reasoningSummaries.length > 0) {
      lines.push('', '**Reasoning:**', '', fencedBlock(message.reasoningSummaries.join('\n\n')))
    }
    if (message.error) {
      const description = message.error.description ? `: ${inlineText(message.error.description)}` : ''
      lines.push('', `**Error (${inlineText(message.error.type)})**${description}`)
    }
    appendAttachmentList(lines, 'Attachments:', message.attachments)
  }

  return `${lines.join('\n')}\n`
}

/**
 * Serialize the in-state conversation to pretty-printed JSON. The envelope
 * records the conversation id, the export timestamp, and the messages. Loading
 * placeholders are dropped, and each attachment loses its non-serializable
 * `File` handle and its source URL — that URL is a signed storage link or a
 * path on this machine, neither of which belongs in a shared file. Everything
 * else, including the rest of the attachment metadata, is preserved as-is.
 */
export function conversationToJson(
  messages: Message[],
  conversationId: string | null,
  exportedAt: Date = new Date(),
): string {
  const envelope = {
    conversation_id: conversationId,
    exported_at: exportedAt.toISOString(),
    messages: withoutLoadingPlaceholders(messages).map(messageForExport),
  }
  return JSON.stringify(envelope, null, 2)
}

/**
 * Render the conversation as a self-contained HTML transcript. Media the
 * browser can read is embedded as a `data:` URI so the file stays readable
 * offline; anything else is named but not embedded, and its source URL is
 * never written to the document. The stylesheet carries print rules so the
 * saved file can be printed or saved as PDF as-is.
 */
export async function conversationToHtml(
  messages: Message[],
  conversationId: string | null,
  exportedAt: Date = new Date(),
): Promise<string> {
  const exported = withoutLoadingPlaceholders(messages)
  const media = await resolveMedia(exported)
  const summary = [
    `Conversation: ${escapeHtml(conversationId ?? '(unsaved)')}`,
    `Exported: ${escapeHtml(exportedAt.toISOString())}`,
    `Messages: ${exported.length}`,
    attachmentSummary(media),
  ].join('<br />')

  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>CoPyRIT conversation export</title>
<style>${HTML_STYLESHEET}</style>
</head>
<body>
<h1>CoPyRIT conversation export</h1>
<p class="meta">${summary}</p>
${exported.map((message, index) => renderMessage(message, media.perMessage[index])).join('\n')}
</body>
</html>
`
}

/**
 * State, in the document itself, how much of the conversation made it in. An
 * incomplete export must never look complete to whoever it is handed to, so
 * every attachment left out is counted and explained here as well as in place.
 */
function attachmentSummary(media: ResolvedMedia): string {
  const outcomes = media.occurrences
  if (outcomes.length === 0) {
    return 'Attachments: none'
  }
  const embedded = outcomes.filter((outcome) => outcome.source !== null).length
  const omitted = outcomes.length - embedded
  if (omitted === 0) {
    return `Attachments: ${embedded} of ${outcomes.length} embedded`
  }
  const reasons = OMISSION_ORDER.filter((reason) =>
    outcomes.some((outcome) => outcome.source === null && outcome.reason === reason),
  ).map((reason) => {
    const count = outcomes.filter((outcome) => outcome.source === null && outcome.reason === reason).length
    return `${count} ${OMISSION_REASONS[reason]}`
  })
  return (
    `Attachments: ${embedded} of ${outcomes.length} embedded` +
    `<br /><span class="omissions">${omitted} not embedded (${reasons.join('; ')})</span>`
  )
}

/**
 * Build a filesystem-safe filename for an exported conversation, e.g.
 * `copyrit-conversation-<id>-<timestamp>.md`. Falls back to a name without the
 * id when the conversation has none.
 */
export function buildExportFilename(
  conversationId: string | null,
  format: ExportFormat,
  now: Date = new Date(),
): string {
  const timestamp = now.toISOString().slice(0, 23).replace(/[:.]/g, '-')
  const extension = FILE_EXTENSIONS[format]
  const sanitizedId = conversationId ? conversationId.replace(/[^A-Za-z0-9._-]/g, '_') : ''
  return sanitizedId
    ? `copyrit-conversation-${sanitizedId}-${timestamp}.${extension}`
    : `copyrit-conversation-${timestamp}.${extension}`
}

/**
 * Trigger a browser download of `content` as `filename`. Uses the Blob → object
 * URL → anchor-click idiom and always revokes the object URL, even if the click
 * throws.
 */
export function downloadTextFile(content: string, filename: string, mimeType: string): void {
  const blob = new Blob([content], { type: mimeType })
  const objectUrl = URL.createObjectURL(blob)
  try {
    const link = document.createElement('a')
    link.href = objectUrl
    link.download = filename
    document.body.appendChild(link)
    try {
      link.click()
    } finally {
      link.remove()
    }
  } finally {
    URL.revokeObjectURL(objectUrl)
  }
}

function withoutLoadingPlaceholders(messages: Message[]): Message[] {
  return messages.filter((message) => !message.isLoading)
}

/**
 * Strip every copy of an attachment a message carries. Media lives in two
 * places: the flat `attachments` list and the `displayPieces` entry it was
 * rendered from. They hold the same object, so sanitizing only the flat list
 * leaves the signed URL sitting in the nested one.
 */
function messageForExport(message: Message): Message {
  const next: Message = { ...message }
  if (message.attachments) {
    next.attachments = message.attachments.map(attachmentWithoutFile)
  }
  if (message.originalAttachments) {
    next.originalAttachments = message.originalAttachments.map(attachmentWithoutFile)
  }
  if (message.displayPieces) {
    next.displayPieces = message.displayPieces.map((piece) =>
      piece.type === 'media' && piece.attachment
        ? { ...piece, attachment: attachmentWithoutFile(piece.attachment) }
        : piece,
    )
  }
  return next
}

/**
 * Strip an attachment down to what is safe to write into a shared file: the
 * `File` handle can't be serialized, and the source URL is a short-lived signed
 * storage link or an absolute path on the operator's machine, so neither
 * belongs in a document that gets mailed around. Inline `data:` values stay —
 * there the URL is the payload, not a pointer to it.
 */
function attachmentWithoutFile(attachment: MessageAttachment): MessageAttachment {
  const next = { ...attachment }
  delete next.file
  if (!next.url.startsWith('data:')) {
    next.url = ''
  }
  return next
}

function appendAttachmentList(
  lines: string[],
  heading: string,
  attachments: MessageAttachment[] | undefined,
): void {
  if (!attachments || attachments.length === 0) {
    return
  }
  lines.push('', `**${heading}**`, '')
  for (const attachment of attachments) {
    lines.push(`- ${inlineText(attachment.type)}: ${inlineText(attachment.name)} (${inlineText(attachment.mimeType)})`)
  }
}

function inlineText(value: string): string {
  return value.replace(/[\r\n]+/g, ' ')
}

function fencedBlock(content: string): string {
  const longestRun = longestBacktickRun(content)
  const fence = '`'.repeat(Math.max(3, longestRun + 1))
  return `${fence}\n${content}\n${fence}`
}

function longestBacktickRun(content: string): number {
  let longest = 0
  let current = 0
  for (let i = 0; i < content.length; i++) {
    if (content[i] === '`') {
      current += 1
      if (current > longest) {
        longest = current
      }
    } else {
      current = 0
    }
  }
  return longest
}

/** Why an attachment is named in the document instead of embedded in it. */
type OmissionReason = 'unreadable' | 'remote' | 'too-large' | 'no-room' | 'not-embeddable'

/** `source` is the escaped text written into the document, which is what it costs. */
type ResolvedAttachment = { source: string } | { source: null; reason: OmissionReason }

/**
 * One resolved outcome per attachment slot, held in the same shape as the
 * messages so rendering reads each occurrence by position. Position, not object
 * identity, is what the document is built from: the same attachment object can
 * appear in two places and each place is written, counted, and paid for.
 */
type ResolvedMedia = {
  perMessage: { attachments: ResolvedAttachment[]; originalAttachments: ResolvedAttachment[] }[]
  occurrences: ResolvedAttachment[]
}

/**
 * Read every attachment in the conversation, resolving each to the text that
 * will be written for it or to the reason its bytes were left out. Resolution
 * is the only step that touches the network; rendering stays a pure function of
 * the result.
 *
 * Attachments are read one at a time so a long conversation cannot open a
 * fetch per attachment at once, and identical sources are read once and
 * shared, so repeating one costs a write but not a second fetch.
 *
 * The budget is spent first come, first served in conversation order, and
 * within a message on the converted attachment before the original it was
 * converted from, so the result the operator is sharing wins the space when it
 * is tight. Choosing by size would fit a few more attachments but would leave a
 * reader no way to tell why one of them made it and the next did not.
 */
async function resolveMedia(messages: Message[]): Promise<ResolvedMedia> {
  const perMessage: ResolvedMedia['perMessage'] = []
  const occurrences: ResolvedAttachment[] = []
  const seen = new Map<string, ResolvedAttachment>()
  let remaining = MAX_TOTAL_INLINE_CHARACTERS

  const resolveList = async (list: MessageAttachment[] | undefined): Promise<ResolvedAttachment[]> => {
    const results: ResolvedAttachment[] = []
    for (const attachment of list ?? []) {
      const key = `${attachment.type}\u0000${attachment.mimeType}\u0000${attachment.url}`
      // A pending upload carries its own bytes, so it can't be keyed by URL.
      const shared = attachment.file ? undefined : seen.get(key)
      // Every occurrence is written into the document, so every occurrence is
      // billed — reading it once saves the fetch, not the space.
      const outcome = shared ?? (await resolveAttachment(attachment, remaining))
      const charged: ResolvedAttachment =
        outcome.source !== null && outcome.source.length > remaining
          ? { source: null, reason: 'no-room' }
          : outcome
      if (charged.source !== null) {
        remaining -= charged.source.length
      }
      results.push(charged)
      occurrences.push(charged)
      if (!attachment.file && !shared) {
        seen.set(key, outcome)
      }
    }
    return results
  }

  for (const message of messages) {
    perMessage.push({
      attachments: await resolveList(message.attachments),
      originalAttachments: await resolveList(message.originalAttachments),
    })
  }
  return { perMessage, occurrences }
}

/**
 * Resolve one attachment to an embeddable `data:` URI, or report why it could
 * not be embedded. Already-inline values are reused as-is, pending uploads are
 * read from their local `File`, and everything else is fetched only from the
 * media endpoint — a cross-origin or `blob:` URL is blocked by the app's own
 * content security policy, and any other same-origin path is answered by the
 * single-page app rather than by media bytes.
 */
async function resolveAttachment(
  attachment: MessageAttachment,
  remaining: number,
): Promise<ResolvedAttachment> {
  // Only inert media is embedded. A file attachment can hold active content,
  // and this artifact is meant to be shared, so files are named instead.
  if (attachment.type === 'file') {
    return { source: null, reason: 'not-embeddable' }
  }
  try {
    if (attachment.url.startsWith('data:')) {
      const inlineBytes = dataUriByteLength(attachment.url)
      if (inlineBytes === 0) {
        return { source: null, reason: 'unreadable' }
      }
      // An inline value is written escaped, so the escaped text is its cost.
      const source = escapeHtml(attachment.url)
      const refusal = budgetRefusal(inlineBytes, source.length, remaining)
      return refusal ? { source: null, reason: refusal } : { source }
    }
    if (attachment.file) {
      return await blobToDataUri(attachment.file, attachment.mimeType, remaining)
    }
    if (!isMediaEndpointUrl(attachment.url)) {
      return {
        source: null,
        reason: isRemoteStorageUrl(attachment.url) ? 'remote' : 'unreadable',
      }
    }
    const response = await fetch(attachment.url)
    if (!response.ok) {
      await discardBody(response)
      return { source: null, reason: 'unreadable' }
    }
    // Check the advertised length before reading the body, so an oversized
    // response is cancelled rather than downloaded only to be discarded.
    const declaredBytes = Number(response.headers?.get('content-length'))
    const declaredRefusal = Number.isFinite(declaredBytes)
      ? budgetRefusal(declaredBytes, dataUriLength(attachment.mimeType, declaredBytes), remaining)
      : null
    if (declaredBytes > 0 && declaredRefusal) {
      await discardBody(response)
      return { source: null, reason: declaredRefusal }
    }
    return await blobToDataUri(await response.blob(), attachment.mimeType, remaining)
  } catch {
    return { source: null, reason: 'unreadable' }
  }
}

/**
 * Close a response the export has decided not to read. Returning early only
 * drops the reference, and the browser goes on pulling the whole body off the
 * network. Cancelling can itself fail, which must not replace the reason the
 * caller is about to report.
 */
async function discardBody(response: Response): Promise<void> {
  try {
    await response.body?.cancel()
  } catch {
    // The body is being abandoned either way.
  }
}

/**
 * Report why a payload cannot be embedded, or `null` when it can. One limit is
 * the size of the attachment itself; the other is the room left in the
 * document. They are told apart so the reader is given the true reason.
 */
function budgetRefusal(bytes: number, writtenLength: number, remaining: number): OmissionReason | null {
  if (bytes > MAX_INLINE_ATTACHMENT_BYTES) {
    return 'too-large'
  }
  return writtenLength > remaining ? 'no-room' : null
}

/** Characters a `data:` URI occupies once `bytes` are base64-encoded behind its prefix. */
function dataUriLength(mimeType: string, bytes: number): number {
  return `data:${mimeType || 'application/octet-stream'};base64,`.length + Math.ceil(bytes / 3) * 4
}

/**
 * Decoded size of a `data:` URI payload — the bytes the media itself is made
 * of, which is what the per-attachment limit is expressed in. The text the URI
 * is written in is a separate cost, and the document budget already charges it
 * in full, so measuring it here as well would refuse media that is comfortably
 * within the limit: a four mebibyte image escape-encoded into twelve mebibytes
 * of text is still a four mebibyte image. Percent escapes therefore count as
 * the single byte each decodes to, literal characters cost their UTF-8 width,
 * and base64 whitespace is formatting rather than data.
 */
function dataUriByteLength(dataUri: string): number {
  const comma = dataUri.indexOf(',')
  if (comma === -1) {
    return 0
  }
  const payload = dataUri.slice(comma + 1)
  if (!/;base64$/i.test(dataUri.slice(0, comma))) {
    return percentDecodedByteLength(payload)
  }
  return base64PayloadBytes(payload)
}

/**
 * Bytes behind percent-encoded text, counted in one pass. Decoding first would
 * copy a payload that can be megabytes long, and would throw on the malformed
 * escapes a conversation is perfectly capable of carrying.
 */
function percentDecodedByteLength(payload: string): number {
  let bytes = 0
  let index = 0
  while (index < payload.length) {
    if (payload.charCodeAt(index) === 0x25 && isHexPair(payload, index + 1)) {
      bytes += 1
      index += 3
      continue
    }
    // A malformed escape is left as the literal characters it is written in.
    const code = payload.codePointAt(index) as number
    bytes += code < 0x80 ? 1 : code < 0x800 ? 2 : code < 0x10000 ? 3 : 4
    index += code < 0x10000 ? 1 : 2
  }
  return bytes
}

function isHexPair(text: string, at: number): boolean {
  return at + 1 < text.length && isHexDigit(text.charCodeAt(at)) && isHexDigit(text.charCodeAt(at + 1))
}

function isHexDigit(code: number): boolean {
  return (code >= 0x30 && code <= 0x39) || (code >= 0x41 && code <= 0x46) || (code >= 0x61 && code <= 0x66)
}

/**
 * Bytes behind a base64 payload, counted in one pass so a line-wrapped payload
 * several megabytes long does not have to be copied into a stripped duplicate
 * of itself just to be measured.
 */
function base64PayloadBytes(payload: string): number {
  let significant = 0
  let padding = 0
  for (let index = 0; index < payload.length; index += 1) {
    const code = payload.charCodeAt(index)
    // Whitespace is formatting rather than data: base64 may arrive wrapped.
    if (code === 0x20 || (code >= 0x09 && code <= 0x0d)) {
      continue
    }
    significant += 1
    // '=' only pads the end, so any run of it is broken by real data.
    padding = code === 0x3d ? padding + 1 : 0
  }
  return Math.max(0, Math.floor((significant * 3) / 4) - Math.min(padding, 2))
}

/** Encode a blob as a `data:` URI, skipping empty and oversized payloads. */
async function blobToDataUri(blob: Blob, mimeType: string, remaining: number): Promise<ResolvedAttachment> {
  if (blob.size === 0) {
    return { source: null, reason: 'unreadable' }
  }
  const refusal = budgetRefusal(blob.size, dataUriLength(mimeType || blob.type, blob.size), remaining)
  if (refusal) {
    return { source: null, reason: refusal }
  }
  const base64 = await fileToBase64(blob)
  if (!base64) {
    return { source: null, reason: 'unreadable' }
  }
  return { source: escapeHtml(`data:${mimeType || blob.type || 'application/octet-stream'};base64,${base64}`) }
}

function isMediaEndpointUrl(url: string): boolean {
  try {
    const parsed = new URL(url, window.location.origin)
    return (
      (parsed.protocol === 'http:' || parsed.protocol === 'https:') &&
      parsed.origin === window.location.origin &&
      parsed.pathname === MEDIA_ENDPOINT_PATH
    )
  } catch {
    return false
  }
}

/**
 * True when the value points at another origin, which is where a deployment
 * backed by cloud storage keeps its media. Reading across origins is refused,
 * so naming one of these is a deliberate choice rather than a failed read, and
 * the reader is told so instead of being shown an error.
 */
function isRemoteStorageUrl(url: string): boolean {
  try {
    const parsed = new URL(url, window.location.origin)
    return (
      (parsed.protocol === 'http:' || parsed.protocol === 'https:') &&
      parsed.origin !== window.location.origin
    )
  } catch {
    return false
  }
}

function renderMessage(message: Message, resolved: ResolvedMedia['perMessage'][number]): string {
  const parts = [
    `<header><span class="role">${escapeHtml(ROLE_LABELS[message.role])}</span>` +
      `<span class="timestamp">${escapeHtml(message.timestamp)}</span></header>`,
  ]
  parts.push(...renderBody(message, resolved.attachments))
  if (message.originalContent != null && message.originalContent !== message.content) {
    parts.push(labelled('Original (before conversion):', `<pre>${escapeHtml(message.originalContent)}</pre>`))
  }
  parts.push(
    renderAttachments('Original attachments (before conversion):', message.originalAttachments, resolved.originalAttachments),
  )
  if (message.reasoningSummaries && message.reasoningSummaries.length > 0) {
    parts.push(labelled('Reasoning:', `<pre>${escapeHtml(message.reasoningSummaries.join('\n\n'))}</pre>`))
  }
  if (message.error) {
    parts.push(renderError(message.error))
  }
  return `<article class="message">\n${parts.filter(Boolean).join('\n')}\n</article>`
}

/**
 * Converted text and media in the order the chat shows them.
 *
 * Backend messages carry `displayPieces`, where text and media alternate; the
 * flat `content` field has already joined every text piece together, so a
 * message that reads "text, image, text" in the chat would otherwise export as
 * both texts followed by the image. Messages the frontend builds itself — an
 * optimistic send, awaiting its backend echo — have no pieces, so the flat
 * fields remain the fallback rather than the primary path.
 */
function renderBody(message: Message, resolved: ResolvedAttachment[]): string[] {
  const flat = () => [renderText(message.content), renderAttachments('Attachments:', message.attachments, resolved)]
  const pieces = message.displayPieces
  if (!pieces || pieces.length === 0) {
    return flat()
  }
  // Every attachment resolved above was billed against the budget and counted
  // in the summary, so the pieces have to account for exactly those. If they
  // ever disagree, the flat list is the only rendering that still shows all of
  // them, and an honest count matters more than the order.
  const mediaPieces = pieces.filter((piece) => piece.type === 'media' && piece.attachment)
  if (mediaPieces.length !== (message.attachments?.length ?? 0)) {
    return flat()
  }
  const parts: string[] = []
  let pending: string[] = []
  // `message.attachments` lists piece attachments in piece order, so the nth
  // media piece that has one is the nth entry — and the nth resolution.
  let attachmentIndex = 0
  const flushText = () => {
    if (pending.length > 0) {
      parts.push(renderText(pending.join('\n')))
      pending = []
    }
  }
  for (const piece of pieces) {
    if (piece.type === 'text') {
      // Consecutive text pieces stay in one block: splitting them would add
      // paragraph breaks the chat never showed.
      if (piece.content !== '') {
        pending.push(piece.content)
      }
      continue
    }
    // A scores-only media piece has no bytes to show.
    if (!piece.attachment) {
      continue
    }
    flushText()
    parts.push(renderAttachment(piece.attachment, resolved[attachmentIndex] ?? { source: null, reason: 'unreadable' }))
    attachmentIndex += 1
  }
  flushText()
  return parts
}

/** A media-only message has no text; an empty block would read as if the model answered with nothing. */
function renderText(content: string): string {
  return content.trim() === '' ? '' : `<pre>${escapeHtml(content)}</pre>`
}

function renderError(error: MessageError): string {
  const description = error.description ? `: ${escapeHtml(error.description)}` : ''
  return `<p class="error">Error (${escapeHtml(error.type)})${description}</p>`
}

function renderAttachments(
  heading: string,
  attachments: MessageAttachment[] | undefined,
  resolved: ResolvedAttachment[],
): string {
  if (!attachments || attachments.length === 0) {
    return ''
  }
  const rendered = attachments.map((attachment, index) =>
    renderAttachment(attachment, resolved[index] ?? { source: null, reason: 'unreadable' }),
  )
  return labelled(heading, rendered.join('\n'))
}

function renderAttachment(attachment: MessageAttachment, resolved: ResolvedAttachment): string {
  const caption = escapeHtml(`${attachment.name} (${attachment.mimeType})`)
  if (resolved.source === null) {
    // Say why the bytes are missing, so nobody mistakes an omission for a
    // message that simply had nothing in it.
    return `<p class="placeholder">[${MEDIA_LABELS[attachment.type]}: ${caption} — ${OMISSION_REASONS[resolved.reason]}]</p>`
  }
  const source = resolved.source
  const label = escapeHtml(attachment.name)
  const body =
    attachment.type === 'image'
      ? `<img src="${source}" alt="${label}" />`
      : attachment.type === 'audio'
        ? `<audio controls src="${source}" aria-label="${label}"></audio>`
        : `<video controls src="${source}" aria-label="${label}"></video>`
  return `<figure>${body}<figcaption>${caption}</figcaption></figure>`
}

function labelled(heading: string, body: string): string {
  return `<p class="label">${escapeHtml(heading)}</p>\n${body}`
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}
