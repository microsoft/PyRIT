import type { Message, MessageDisplayPiece } from '@/types'

export interface RenderMessagePiece {
  readonly piece: MessageDisplayPiece
  readonly groupId: string | number
  readonly markdownTestId: string
}

export function formatStructuredMessage(text: string): string | null {
  const trimmed = text.trim()
  if (!trimmed) return null

  const first = trimmed[0]
  const last = trimmed[trimmed.length - 1]
  if (!((first === '{' && last === '}') || (first === '[' && last === ']'))) {
    return null
  }

  try {
    JSON.parse(trimmed)
  } catch {
    return null
  }
  return indentJson(trimmed)
}

const JSON_WHITESPACE = ' \t\n\r'

// Indents valid JSON text the way JSON.stringify(value, null, 2) does, but copies each
// number as written: JSON.parse turns numbers into doubles, which rounds integers above
// Number.MAX_SAFE_INTEGER. Strings still go through JSON.parse and JSON.stringify, so
// escapes such as é are shown as the characters they stand for.
function indentJson(json: string): string {
  let out = ''
  let depth = 0
  const newline = () => '\n' + '  '.repeat(depth)

  for (let i = 0; i < json.length; i++) {
    const ch = json[i]
    if (JSON_WHITESPACE.includes(ch)) continue

    if (ch === '"') {
      let end = i + 1
      while (json[end] !== '"') end += json[end] === '\\' ? 2 : 1
      out += JSON.stringify(JSON.parse(json.slice(i, end + 1)))
      i = end
    } else if (ch === '{' || ch === '[') {
      let next = i + 1
      while (JSON_WHITESPACE.includes(json[next])) next++
      if (json[next] === (ch === '{' ? '}' : ']')) {
        out += ch + json[next]
        i = next
      } else {
        depth++
        out += ch + newline()
      }
    } else if (ch === '}' || ch === ']') {
      depth--
      out += newline() + ch
    } else if (ch === ',') {
      out += ',' + newline()
    } else if (ch === ':') {
      out += ': '
    } else {
      // A number or one of the literals true, false and null
      let end = i + 1
      while (end < json.length && !',:]}'.includes(json[end]) && !JSON_WHITESPACE.includes(json[end])) end++
      out += json.slice(i, end)
      i = end - 1
    }
  }
  return out
}

export function getRenderMessagePieces(
  message: Message,
  messageIndex: number,
): RenderMessagePiece[] {
  if (message.displayPieces) {
    const hasMultiplePieces = message.displayPieces.length > 1
    return message.displayPieces.map((piece) => ({
      piece,
      groupId: hasMultiplePieces ? `${messageIndex}-piece-${piece.pieceIndex}` : messageIndex,
      markdownTestId: `message-markdown-${messageIndex}-${piece.pieceIndex}`,
    }))
  }

  const pieces: RenderMessagePiece[] = []
  if (message.content || message.scores?.length) {
    pieces.push({
      piece: {
        type: 'text',
        pieceId: `message-${messageIndex}-text`,
        pieceIndex: 0,
        content: message.content,
        scores: message.scores,
      },
      groupId: messageIndex,
      markdownTestId: `message-markdown-${messageIndex}`,
    })
  }

  message.attachments?.forEach((attachment, attachmentIndex) => {
    pieces.push({
      piece: {
        type: 'media',
        pieceId: attachment.pieceId ?? `message-${messageIndex}-attachment-${attachmentIndex}`,
        pieceIndex: attachmentIndex,
        attachment,
      },
      groupId: `${messageIndex}-att-${attachmentIndex}`,
      markdownTestId: `message-markdown-${messageIndex}-att-${attachmentIndex}`,
    })
  })

  return pieces
}
