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
    // Parsing only validates the input; re-serializing the parsed value would
    // round integers above Number.MAX_SAFE_INTEGER, so print from the source.
    JSON.parse(trimmed)
  } catch {
    return null
  }
  return prettyPrintJson(trimmed)
}

const JSON_TOKEN = /"(?:[^"\\]|\\.)*"|[{}[\],:]|[^\s{}[\],:"]+/g

/**
 * Pretty-print already-validated JSON text with 2-space indentation while
 * keeping scalar tokens (notably number lexemes) exactly as written.
 */
function prettyPrintJson(text: string): string {
  const tokens = text.match(JSON_TOKEN) ?? []
  let out = ''
  let depth = 0
  const newline = () => `\n${'  '.repeat(depth)}`

  for (let i = 0; i < tokens.length; i++) {
    const token = tokens[i]
    if (token === '{' || token === '[') {
      const next = tokens[i + 1]
      if (next === (token === '{' ? '}' : ']')) {
        out += token + next
        i++
      } else {
        depth++
        out += token + newline()
      }
    } else if (token === '}' || token === ']') {
      depth--
      out += newline() + token
    } else if (token === ',') {
      out += ',' + newline()
    } else if (token === ':') {
      out += ': '
    } else if (token.startsWith('"')) {
      out += JSON.stringify(JSON.parse(token))
    } else {
      out += token
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
