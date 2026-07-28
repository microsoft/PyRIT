import type { Message, MessageAttachment } from '../types'

export type ExportFormat = 'markdown' | 'json'

const FILE_EXTENSIONS: Record<ExportFormat, string> = {
  markdown: 'md',
  json: 'json',
}

export const EXPORT_MIME_TYPES: Record<ExportFormat, string> = {
  markdown: 'text/markdown;charset=utf-8',
  json: 'application/json;charset=utf-8',
}

const ROLE_LABELS: Record<Message['role'], string> = {
  user: 'User',
  assistant: 'Assistant',
  simulated_assistant: 'Simulated Assistant',
  system: 'System',
}

/**
 * Serialize, name, and download the currently viewed conversation in one call.
 * Markdown and JSON share a single timestamp so the filename and the document
 * body agree.
 */
export function exportConversation({
  messages,
  conversationId,
  format,
  now = new Date(),
}: {
  messages: Message[]
  conversationId: string | null
  format: ExportFormat
  now?: Date
}): void {
  const content =
    format === 'markdown'
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
