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

/**
 * Largest attachment inlined into an HTML export. Base64 inflates bytes by a
 * third, so anything above this is listed by name instead of embedded to keep
 * the file openable.
 */
const MAX_INLINE_ATTACHMENT_BYTES = 10 * 1024 * 1024

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
 * Serialize the in-state conversation to pretty-printed JSON, exporting exactly
 * what the GUI holds (WYSIWYG). The envelope records the conversation id, the
 * export timestamp, and the messages. Loading placeholders are dropped and the
 * non-serializable `File` handle is removed from each attachment; every other
 * field (including attachment metadata) is preserved as-is.
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
  ].join('<br />')

  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>CoPyRIT conversation export</title>
<style>${HTML_STYLESHEET}</style>
</head>
<body>
<h1>CoPyRIT conversation export</h1>
<p class="meta">${summary}</p>
${exported.map((message) => renderMessage(message, media)).join('\n')}
</body>
</html>
`
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

function messageForExport(message: Message): Message {
  if (!message.attachments && !message.originalAttachments) {
    return message
  }
  const next: Message = { ...message }
  if (message.attachments) {
    next.attachments = message.attachments.map(attachmentWithoutFile)
  }
  if (message.originalAttachments) {
    next.originalAttachments = message.originalAttachments.map(attachmentWithoutFile)
  }
  return next
}

function attachmentWithoutFile(attachment: MessageAttachment): MessageAttachment {
  const next = { ...attachment }
  delete next.file
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

type ResolvedMedia = Map<MessageAttachment, string | null>

/**
 * Read every attachment in the conversation, mapping each to a `data:` URI or
 * to `null` when its bytes cannot be read. Resolution is the only step that
 * touches the network; rendering stays a pure function of the result.
 */
async function resolveMedia(messages: Message[]): Promise<ResolvedMedia> {
  const attachments = messages.flatMap((message) => [
    ...(message.attachments ?? []),
    ...(message.originalAttachments ?? []),
  ])
  const resolved = await Promise.all(
    attachments.map(async (attachment) => [attachment, await attachmentToDataUri(attachment)] as const),
  )
  return new Map(resolved)
}

/**
 * Resolve one attachment to an embeddable `data:` URI, or `null` when the
 * bytes are unavailable. Already-inline values are reused as-is, pending
 * uploads are read from their local `File`, and everything else is fetched
 * only from the media endpoint — a cross-origin or `blob:` URL is blocked by
 * the app's own content security policy, and any other same-origin path is
 * answered by the single-page app rather than by media bytes.
 */
async function attachmentToDataUri(attachment: MessageAttachment): Promise<string | null> {
  // Only inert media is embedded. A file attachment can hold active content,
  // and this artifact is meant to be shared, so files are named instead.
  if (attachment.type === 'file') {
    return null
  }
  try {
    if (attachment.url.startsWith('data:')) {
      return withinInlineCap(attachment.url) ? attachment.url : null
    }
    if (attachment.file) {
      return await blobToDataUri(attachment.file, attachment.mimeType)
    }
    if (!isMediaEndpointUrl(attachment.url)) {
      return null
    }
    const response = await fetch(attachment.url)
    if (!response.ok) {
      return null
    }
    return await blobToDataUri(await response.blob(), attachment.mimeType)
  } catch {
    return null
  }
}

/**
 * Report whether an already-inline `data:` URI is small enough to embed. Only
 * base64 payloads carry a measurable size; anything else is short by nature.
 */
function withinInlineCap(dataUri: string): boolean {
  const marker = ';base64,'
  const start = dataUri.indexOf(marker)
  if (start === -1) {
    return true
  }
  const payload = dataUri.slice(start + marker.length)
  const padding = payload.endsWith('==') ? 2 : payload.endsWith('=') ? 1 : 0
  const bytes = Math.floor((payload.length * 3) / 4) - padding
  return bytes > 0 && bytes <= MAX_INLINE_ATTACHMENT_BYTES
}

/** Encode a blob as a `data:` URI, skipping empty and oversized payloads. */
async function blobToDataUri(blob: Blob, mimeType: string): Promise<string | null> {
  if (blob.size === 0 || blob.size > MAX_INLINE_ATTACHMENT_BYTES) {
    return null
  }
  const base64 = await fileToBase64(blob)
  if (!base64) {
    return null
  }
  return `data:${mimeType || blob.type || 'application/octet-stream'};base64,${base64}`
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

function renderMessage(message: Message, media: ResolvedMedia): string {
  const parts = [
    `<header><span class="role">${escapeHtml(ROLE_LABELS[message.role])}</span>` +
      `<span class="timestamp">${escapeHtml(message.timestamp)}</span></header>`,
  ]
  // A media-only message has no text; an empty block would read as if the
  // model answered with nothing.
  if (message.content.trim() !== '') {
    parts.push(`<pre>${escapeHtml(message.content)}</pre>`)
  }
  if (message.originalContent != null && message.originalContent !== message.content) {
    parts.push(labelled('Original (before conversion):', `<pre>${escapeHtml(message.originalContent)}</pre>`))
  }
  parts.push(renderAttachments('Original attachments (before conversion):', message.originalAttachments, media))
  if (message.reasoningSummaries && message.reasoningSummaries.length > 0) {
    parts.push(labelled('Reasoning:', `<pre>${escapeHtml(message.reasoningSummaries.join('\n\n'))}</pre>`))
  }
  if (message.error) {
    parts.push(renderError(message.error))
  }
  parts.push(renderAttachments('Attachments:', message.attachments, media))
  return `<article class="message">\n${parts.filter(Boolean).join('\n')}\n</article>`
}

function renderError(error: MessageError): string {
  const description = error.description ? `: ${escapeHtml(error.description)}` : ''
  return `<p class="error">Error (${escapeHtml(error.type)})${description}</p>`
}

function renderAttachments(
  heading: string,
  attachments: MessageAttachment[] | undefined,
  media: ResolvedMedia,
): string {
  if (!attachments || attachments.length === 0) {
    return ''
  }
  const rendered = attachments.map((attachment) => renderAttachment(attachment, media.get(attachment) ?? null))
  return labelled(heading, rendered.join('\n'))
}

function renderAttachment(attachment: MessageAttachment, dataUri: string | null): string {
  const caption = escapeHtml(`${attachment.name} (${attachment.mimeType})`)
  if (!dataUri) {
    return `<p class="placeholder">[${MEDIA_LABELS[attachment.type]}: ${caption}]</p>`
  }
  // The MIME type is echoed into the data URI, so escape it like any other
  // untrusted value before it lands in an attribute.
  const source = escapeHtml(dataUri)
  const body =
    attachment.type === 'image'
      ? `<img src="${source}" alt="${escapeHtml(attachment.name)}" />`
      : attachment.type === 'audio'
        ? `<audio controls src="${source}"></audio>`
        : `<video controls src="${source}"></video>`
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
