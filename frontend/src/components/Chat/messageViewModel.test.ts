import type { Message } from '@/types'

import { formatStructuredMessage, getRenderMessagePieces } from './messageViewModel'

describe('messageViewModel', () => {
  describe('formatStructuredMessage', () => {
    it('should format JSON objects and arrays', () => {
      expect(formatStructuredMessage('{"answer":true}')).toBe('{\n  "answer": true\n}')
      expect(formatStructuredMessage('[1,2]')).toBe('[\n  1,\n  2\n]')
    })

    it('should leave scalar and malformed JSON as plain text', () => {
      expect(formatStructuredMessage('true')).toBeNull()
      expect(formatStructuredMessage('{not-json}')).toBeNull()
    })
  })

  describe('getRenderMessagePieces', () => {
    it('should preserve explicit display-piece order and identities', () => {
      const message = {
        displayPieces: [
          { type: 'text', pieceId: 'first', pieceIndex: 2, content: 'First' },
          { type: 'text', pieceId: 'second', pieceIndex: 5, content: 'Second' },
        ],
      } as Message

      expect(getRenderMessagePieces(message, 3)).toEqual([
        {
          piece: message.displayPieces?.[0],
          groupId: '3-piece-2',
          markdownTestId: 'message-markdown-3-2',
        },
        {
          piece: message.displayPieces?.[1],
          groupId: '3-piece-5',
          markdownTestId: 'message-markdown-3-5',
        },
      ])
    })

    it('should map legacy content and attachments without mixing their scores', () => {
      const message = {
        content: 'Converted text',
        scores: [{ id: 'score-1' }],
        attachments: [{ type: 'image', name: 'chart', url: '/chart.png' }],
      } as Message

      const pieces = getRenderMessagePieces(message, 4)

      expect(pieces).toHaveLength(2)
      expect(pieces[0].piece.scores).toBe(message.scores)
      expect(pieces[1].piece.scores).toBeUndefined()
      expect(pieces[1].groupId).toBe('4-att-0')
    })
  })
})
