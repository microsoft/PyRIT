import type { Message, MessageDisplayPiece } from '@/types'

export interface RenderMessagePiece {
  readonly piece: MessageDisplayPiece
  readonly groupId: string | number
  readonly markdownTestId: string
}

/**
 * If the trimmed text is a JSON object or array, return a 2-space pretty-printed
 * version of it; otherwise return null. Used to render structured assistant
 * responses (e.g. PromptShield verdicts) as readable JSON instead of a single
 * unformatted line.
 *
 * Preserves exact numeric lexemes (including integers exceeding Number.MAX_SAFE_INTEGER)
 * so 64-bit identifiers and large numeric payloads are not rounded by JavaScript Number.
 */
export function formatStructuredMessage(text: string): string | null {
  const trimmed = text.trim()
  if (!trimmed) return null

  const first = trimmed[0]
  const last = trimmed[trimmed.length - 1]
  if (!((first === '{' && last === '}') || (first === '[' && last === ']'))) {
    return null
  }

  try {
    // Validate that the original text is strictly valid JSON syntax
    const originalParsed = JSON.parse(trimmed)
    if (typeof originalParsed !== 'object' || originalParsed === null) {
      return null
    }

    // Tokenize JSON strings and numeric literals so numbers inside strings are not touched
    // while numeric literals are temporarily protected from IEEE 754 precision loss during parsing.
    const numbers: string[] = []
    const tokenRegex = /"(?:[^"\\]|\\.)*"|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/g

    // Generate a unique token prefix that cannot collide with any substring in the input
    let nonce = `_PYRIT_NUM_${Math.random().toString(36).slice(2)}_${Date.now().toString(36)}_`
    while (trimmed.includes(nonce)) {
      nonce = `_PYRIT_NUM_${Math.random().toString(36).slice(2)}_${Date.now().toString(36)}_`
    }

    const placeholderJson = trimmed.replace(tokenRegex, (match) => {
      if (match.startsWith('"')) {
        return match // String literal, preserve untouched
      }
      // Numeric literal: replace with a placeholder string that parses losslessly
      const id = numbers.length
      numbers.push(match)
      return `"${nonce}${id}__"`
    })

    if (numbers.length === 0) {
      return JSON.stringify(originalParsed, null, 2)
    }

    // Validate that the substituted JSON is structurally valid JSON (object or array)
    const parsed = JSON.parse(placeholderJson)
    if (typeof parsed !== 'object' || parsed === null) {
      return null
    }

    const formatted = JSON.stringify(parsed, null, 2)
    // Restore the exact numeric lexemes in place of the placeholders
    const restoreRegex = new RegExp(`"${nonce}(\\d+)__"`, 'g')
    return formatted.replace(restoreRegex, (match, index) => {
      const numIndex = Number(index)
      return numIndex in numbers ? numbers[numIndex] : match
    })
  } catch {
    return null
  }
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
