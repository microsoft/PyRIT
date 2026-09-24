import type { PieceConversion } from '@/components/Chat/converterTypes'
import {
  applyConvertedValues, buildConverterInputs, buildDraftPieceIds, withDraftIdentity,
} from '@/components/Chat/converterTypes'
import type { MessageAttachment } from '@/types'
import { buildMessagePieces } from '@/utils/messageMapper'

describe('withDraftIdentity', () => {
  it('assigns distinct attachment identities when randomUUID is unavailable', () => {
    const descriptor = Object.getOwnPropertyDescriptor(crypto, 'randomUUID')
    Object.defineProperty(crypto, 'randomUUID', { configurable: true, value: undefined })
    try {
      const attachment: MessageAttachment = {
        type: 'image', name: 'same.png', url: 'same.png', mimeType: 'image/png',
      }
      const first = withDraftIdentity(attachment)
      const second = withDraftIdentity(attachment)
      expect(first.draftId).toEqual(expect.any(String))
      expect(first.draftId).not.toBe(second.draftId)
      expect(withDraftIdentity(first).draftId).toBe(first.draftId)
    } finally {
      if (descriptor) Object.defineProperty(crypto, 'randomUUID', descriptor)
      else Reflect.deleteProperty(crypto, 'randomUUID')
    }
  })
})

function makeConversion(
  pieceId: string,
  converterInstanceIds: string[],
): PieceConversion {
  return {
    converterInstanceIds,
    convertedDataType: 'text',
    convertedValue: 'converted',
    originalValue: 'original',
    pieceId,
    pieceType: 'image',
  }
}

describe('converter draft mapping', () => {
  it('targets only applied pieces and retains converter order across type changes', () => {
    const pieces = applyConvertedValues(
      ['text', 'image_path', 'audio_path', 'image_path'].map((data_type: string) => ({
        data_type, original_value: 'original',
      })),
      ['text', 'first-image', 'audio', 'second-image'],
      {
        text: makeConversion('text', ['base64']),
        'second-image': makeConversion('second-image', ['compress', 'caption', 'base64']),
        removed: makeConversion('removed', ['zip']),
      },
    )

    expect(pieces[0].applied_converter_ids).toEqual(['base64'])
    expect(pieces[1].applied_converter_ids).toBeUndefined()
    expect(pieces[2].applied_converter_ids).toBeUndefined()
    expect(pieces[3].applied_converter_ids).toEqual(['compress', 'caption', 'base64'])
  })

  describe('applyConvertedValues', () => {
    it('preserves original values and applies exact results by piece identity across type changes', () => {
      const original = [
        { data_type: 'text', original_value: 'original text' },
        { data_type: 'image_path', original_value: 'first.png' },
        { data_type: 'image_path', original_value: 'second.png' },
      ]
      const result = applyConvertedValues(original, ['text', 'first', 'second'], {
        text: { ...makeConversion('text', ['pdf']), convertedValue: 'result.pdf', convertedDataType: 'binary_path' },
        second: { ...makeConversion('second', ['caption']), convertedValue: '' },
      })
      expect(result).toEqual([
        {
          ...original[0], converted_value: 'result.pdf', converted_value_data_type: 'binary_path',
          applied_converter_ids: ['pdf'],
        },
        original[1],
        {
          ...original[2], converted_value: '', converted_value_data_type: 'text',
          applied_converter_ids: ['caption'],
        },
      ])
      expect(original[0]).not.toHaveProperty('converted_value')
    })

    it('rejects mismatched piece identities', () => {
      expect(() => applyConvertedValues([], ['text'], { text: makeConversion('text', ['base64']) }))
        .toThrow('Message pieces do not match')
    })
  })

  it('retains an empty converter list for a manual conversion', () => {
    const pieces = applyConvertedValues(
      [{ data_type: 'text', original_value: 'original' }],
      ['text'],
      { text: makeConversion('text', []) },
    )

    expect(pieces[0].applied_converter_ids).toEqual([])
  })

  it.each(['hello', '   '])('maps duplicate filenames in exactly the message order with text %j', async (text: string) => {
    const attachments: MessageAttachment[] = ['first', 'second'].map((draftId: string) => ({
      draftId,
      type: 'image',
      name: 'same.png',
      url: `${draftId}.png`,
      sourceValue: `${draftId}.png`,
      mimeType: 'image/png',
    }))
    const pieces = await buildMessagePieces(text, attachments)
    const pieceIds = buildDraftPieceIds(text, attachments)
    const converted = applyConvertedValues(pieces, pieceIds, {
      second: makeConversion('second', ['compress']),
    })
    expect(pieceIds).toHaveLength(pieces.length)
    expect(converted.find((piece) => piece.applied_converter_ids)?.original_value).toBe('second.png')

    const afterRemoval = buildDraftPieceIds(text, attachments.slice(1))
    const remaining = await buildMessagePieces(text, attachments.slice(1))
    const convertedRemaining = applyConvertedValues(remaining, afterRemoval, {
      second: makeConversion('second', ['compress']),
    })
    expect(convertedRemaining[text.trim() ? 1 : 0].applied_converter_ids).toEqual(['compress'])
  })

  it('retains actual input data types for generic files and recovered pieces', () => {
    const inputs = buildConverterInputs('', [{
      draftId: 'file',
      type: 'file',
      name: 'prompt.txt',
      url: 'prompt.txt',
      mimeType: 'text/plain',
      sourceValue: 'restored value',
      sourceDataType: 'text',
    }])
    expect(inputs[1]).toEqual(expect.objectContaining({ id: 'file', pieceType: 'file', dataType: 'text' }))
  })

  it('rejects attachments without draft identities rather than guessing piece indexes', () => {
    expect(() => buildDraftPieceIds('', [{
      type: 'image', name: 'image.png', url: 'image.png', mimeType: 'image/png',
    }])).toThrow('Draft attachment is missing its identity')
  })
})
