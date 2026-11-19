import type { Message } from '@/types'

import { formatStructuredMessage, getRenderMessagePieces } from './messageViewModel'

describe('messageViewModel', () => {
  describe('formatStructuredMessage', () => {
    afterEach(() => jest.restoreAllMocks())

    it('should format JSON objects and arrays', () => {
      expect(formatStructuredMessage('{"answer":true}')).toBe('{\n  "answer": true\n}')
      expect(formatStructuredMessage('[1,2]')).toBe('[\n  1,\n  2\n]')
    })

    it('should leave scalar and malformed JSON as plain text', () => {
      expect(formatStructuredMessage('true')).toBeNull()
      expect(formatStructuredMessage('{not-json}')).toBeNull()
      expect(formatStructuredMessage('{1:2}')).toBeNull()
      expect(formatStructuredMessage('[1, 2,]')).toBeNull()
    })

    it('should preserve exact numeric lexemes and large integers without rounding', () => {
      expect(formatStructuredMessage('{"id":9007199254740993}')).toBe('{\n  "id": 9007199254740993\n}')
      expect(formatStructuredMessage('{"large":-9007199254740995}')).toBe('{\n  "large": -9007199254740995\n}')
      expect(formatStructuredMessage('[9007199254740994, 9007199254740997]')).toBe('[\n  9007199254740994,\n  9007199254740997\n]')
    })

    it('should not collide with placeholder-like string values, keys, or escaped sequences', () => {
      const input = '{"literal":"__PYRIT_NUM_0__","id":42}'
      expect(formatStructuredMessage(input)).toBe('{\n  "literal": "__PYRIT_NUM_0__",\n  "id": 42\n}')

      const keyInput = '{"__PYRIT_NUM_0__":42}'
      expect(formatStructuredMessage(keyInput)).toBe('{\n  "__PYRIT_NUM_0__": 42\n}')

      const escapedInput = '{"\\u005f\\u005fPYRIT_NUM_0\\u005f\\u005f":"val","num":9007199254740993}'
      expect(formatStructuredMessage(escapedInput)).toBe('{\n  "__PYRIT_NUM_0__": "val",\n  "num": 9007199254740993\n}')
    })

    it('should not alter numbers within string values', () => {
      const input = '{"desc":"ID 9007199254740993 in text","id":9007199254740993}'
      expect(formatStructuredMessage(input)).toBe('{\n  "desc": "ID 9007199254740993 in text",\n  "id": 9007199254740993\n}')
    })

    it('should format floating point numbers and exponential notation faithfully', () => {
      expect(formatStructuredMessage('{"pi":3.14159,"exp":1e20}')).toBe('{\n  "pi": 3.14159,\n  "exp": 1e20\n}')
    })

    it.each([
      '1.0', '-0', '1E+2', '1e-2', '0.10',
      '0.1000000000000000055511151231257827',
      '9223372036854775807', '-9223372036854775808', '1e400', '1e-400',
    ])('should preserve the exact spelling of %s', (token: string) => {
      expect(formatStructuredMessage(`[${token}]`)).toBe(`[\n  ${token}\n]`)
    })

    it('should preserve numeric spelling in nested objects and arrays', () => {
      const input = '{"values":[1.0,-0,1E+2,0.1000000000000000055511151231257827],"empty":{}}'
      expect(formatStructuredMessage(input)).toBe(
        '{\n  "values": [\n    1.0,\n    -0,\n    1E+2,\n    0.1000000000000000055511151231257827\n  ],\n  "empty": {}\n}',
      )
    })

    it('should keep nested, empty and escaped content compatible with two-space formatting', () => {
      const value = {
        empty: [[], {}],
        nested: [{ text: 'quote " backslash \\ newline \n tab \t caf\u00e9 \u0001 \ud83d\ude00' }],
        number: 42,
      }
      expect(formatStructuredMessage(JSON.stringify(value))).toBe(JSON.stringify(value, null, 2))
    })

    it.each([
      '{"literal":"_PYRIT_NUM_i_0_0__","id":42}',
      '{"literal":"\\u005fPYRIT_NUM_i_0_0__","id":42}',
      '{"\\u005fPYRIT_NUM_i_0_0__":42,"id":7}',
      '{"nested":[{"literal":"\\u005fPYRIT_NUM_i_0_0__"}],"id":42}',
      '{"literal":"_PYRIT_NUM_i_0_0__"}',
      '{"\\u005fPYRIT_NUM_i_0_0__":"kept"}',
    ])('should preserve active marker-like strings and keys in %s', (input: string) => {
      jest.spyOn(Math, 'random').mockReturnValueOnce(0.5).mockReturnValue(0.25)
      jest.spyOn(Date, 'now').mockReturnValue(0)

      expect(formatStructuredMessage(input)).toBe(JSON.stringify(JSON.parse(input), null, 2))
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
