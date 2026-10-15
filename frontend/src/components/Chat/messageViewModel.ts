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
    return JSON.stringify(JSON.parse(trimmed), null, 2)
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
