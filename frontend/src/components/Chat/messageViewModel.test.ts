import type { Message } from '@/types'

import { formatStructuredMessage, getRenderMessagePieces } from './messageViewModel'

describe('messageViewModel', () => {
  describe('formatStructuredMessage', () => {
    it('should format JSON objects and arrays', () => {
      expect(formatStructuredMessage('{"answer":true}')).toBe('{\n  "answer": true\n}')
      expect(formatStructuredMessage('[1,2]')).toBe('[\n  1,\n  2\n]')
    })

    it('should keep number lexemes exactly as written', () => {
      expect(formatStructuredMessage('{"id":9007199254740993}')).toBe('{\n  "id": 9007199254740993\n}')
      expect(formatStructuredMessage('[12345678901234567890,1.0,-0,1E+2,0.10]')).toBe(
        '[\n  12345678901234567890,\n  1.0,\n  -0,\n  1E+2,\n  0.10\n]',
      )
    })

    it('should format nested, empty and escaped content like JSON.stringify', () => {
      const value = {
        a: [],
        b: {},
        c: [{ d: 'quote " backslash \\ newline \n tab \t', e: null }],
        f: 'caf\u00e9 \u0001 \ud83d\ude00',
        g: [[1, [2]], false],
      }
      expect(formatStructuredMessage(JSON.stringify(value))).toBe(JSON.stringify(value, null, 2))
      expect(formatStructuredMessage('{"s":"\\u0041\\ud83d"}')).toBe(
        JSON.stringify(JSON.parse('{"s":"\\u0041\\ud83d"}'), null, 2),
      )
      expect(formatStructuredMessage('{}')).toBe('{}')
      expect(formatStructuredMessage(' [ ] ')).toBe('[]')
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
