import type { PieceConversion } from '@/components/Chat/converterTypes'
import {
  applyConvertedValues, buildConverterInputs, buildDraftPieceIds, buildRequestConverterConfigurations, withDraftIdentity,
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

describe('buildRequestConverterConfigurations', () => {
  it('targets only applied pieces and retains converter order across type changes', () => {
    const configurations = buildRequestConverterConfigurations(
      ['text', 'first-image', 'audio', 'second-image'],
      {
        text: makeConversion('text', ['base64']),
        'second-image': makeConversion('second-image', ['compress', 'caption', 'base64']),
        removed: makeConversion('removed', ['zip']),
      },
    )

    expect(configurations).toEqual([
      {
        converter_ids: ['base64'],
        indexes_to_apply: [0],
      },
      {
        converter_ids: ['compress', 'caption', 'base64'],
        indexes_to_apply: [3],
      },
    ])
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
        { ...original[0], converted_value: 'result.pdf', converted_value_data_type: 'binary_path' },
        original[1],
        { ...original[2], converted_value: '', converted_value_data_type: 'text' },
      ])
      expect(original[0]).not.toHaveProperty('converted_value')
    })

    it('rejects mismatched piece identities', () => {
      expect(() => applyConvertedValues([], ['text'], { text: makeConversion('text', ['base64']) }))
        .toThrow('Message pieces do not match')
    })
  })

  it('skips modalities with an empty pipeline', () => {
    const configurations = buildRequestConverterConfigurations(
      ['text'],
      { text: makeConversion('text', []) },
    )

    expect(configurations).toEqual([])
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
    const configurations = buildRequestConverterConfigurations(pieceIds, {
      second: makeConversion('second', ['compress']),
    })
    expect(pieceIds).toHaveLength(pieces.length)
    expect(pieces[configurations[0].indexes_to_apply![0]].original_value).toBe('second.png')

    const afterRemoval = buildDraftPieceIds(text, attachments.slice(1))
    expect(buildRequestConverterConfigurations(afterRemoval, { second: makeConversion('second', ['compress']) }))
      .toEqual([{ converter_ids: ['compress'], indexes_to_apply: [text.trim() ? 1 : 0] }])
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
