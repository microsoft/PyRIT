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

    it('should keep integers above Number.MAX_SAFE_INTEGER as received', () => {
      expect(formatStructuredMessage('{"id":9007199254740993}')).toBe('{\n  "id": 9007199254740993\n}')
    })

    it('should keep every number as written', () => {
      expect(formatStructuredMessage('[1.0, 1e5, -0, 0.1000000000000000055511151231257827]')).toBe(
        '[\n  1.0,\n  1e5,\n  -0,\n  0.1000000000000000055511151231257827\n]',
      )
    })

    it('should otherwise match JSON.stringify with two spaces', () => {
      const inputs = [
        '{"a":{"b":[1,{"c":null}],"d":{},"e":[ ]},"t":false}',
        ' [ "caf\\u00e9", "say \\"hi\\"\\n", "a/b\\\\c", {"k:,{}[]":"v"} ] ',
        '{\n  "nested": [[], [[]], {"x": [true, false]}]\n}',
      ]
      for (const input of inputs) {
        expect(formatStructuredMessage(input)).toBe(JSON.stringify(JSON.parse(input), null, 2))
      }
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
