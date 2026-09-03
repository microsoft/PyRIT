import type { PieceConversion } from '@/components/Chat/converterTypes'
import { buildRequestConverterConfigurations } from '@/components/Chat/converterTypes'

function makeConversion(
  pieceType: string,
  converterInstanceIds: string[],
): PieceConversion {
  return {
    converterInstanceIds,
    convertedDataType: 'text',
    convertedValue: 'converted',
    originalValue: 'original',
    pieceType,
  }
}

describe('buildRequestConverterConfigurations', () => {
  it('targets every original message-piece index for each modality pipeline', () => {
    const configurations = buildRequestConverterConfigurations(
      [
        { data_type: 'text', original_value: 'Describe these images' },
        { data_type: 'image_path', original_value: 'first.png' },
        { data_type: 'audio_path', original_value: 'sample.wav' },
        { data_type: 'image_path', original_value: 'second.png' },
      ],
      {
        text: makeConversion('text', ['base64']),
        image: makeConversion('image', ['compress', 'caption']),
        file: makeConversion('file', ['zip']),
      },
    )

    expect(configurations).toEqual([
      {
        converter_ids: ['base64'],
        indexes_to_apply: [0],
      },
      {
        converter_ids: ['compress', 'caption'],
        indexes_to_apply: [1, 3],
      },
    ])
  })

  it('skips modalities with an empty pipeline', () => {
    const configurations = buildRequestConverterConfigurations(
      [{ data_type: 'text', original_value: 'hello' }],
      { text: makeConversion('text', []) },
    )

    expect(configurations).toEqual([])
  })
})
