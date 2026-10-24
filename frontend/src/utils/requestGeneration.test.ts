import type { MessagePieceRequest } from '@/types'
import { temperatureDescription, withRequestTemperature } from './requestGeneration'

describe('request temperature', () => {
  it.each(['0', '0.8', '2'])('attaches numeric %s to only one piece', (input: string) => {
    const pieces: MessagePieceRequest[] = [
      { data_type: 'text', original_value: 'hello' },
      { data_type: 'image_path', original_value: 'image.png', prompt_metadata: { video_id: 'keep' } },
    ]
    const result = withRequestTemperature(pieces, input)
    expect(result[0].prompt_metadata).toEqual({ request_temperature: Number(input) })
    expect(result[1].prompt_metadata).toEqual({ video_id: 'keep' })
    expect(pieces[0].prompt_metadata).toBeUndefined()
  })

  it('clears copied options and evidence without losing unrelated metadata', () => {
    const result = withRequestTemperature([{
      data_type: 'text', original_value: 'hello',
      prompt_metadata: { request_temperature: 1.5, _pyrit_temperature: { source: 'request' }, video_id: 'keep' },
    }], '')
    expect(result[0].prompt_metadata).toEqual({ video_id: 'keep' })
  })

  it.each(['NaN', 'Infinity', '-0.1', '2.1', 'not a number'])('rejects %s', (input: string) => {
    expect(() => withRequestTemperature([], input)).toThrow(/finite number between 0 and 2/)
  })

  it.each([
    [0, 'request', 'Temperature: 0 (request override)'],
    [0.2, 'target_default', 'Temperature: 0.2 (target default)'],
    [null, 'provider_default', 'Temperature: provider default (unknown)'],
  ])('describes saved %s from %s', (value, source, expected) => {
    expect(temperatureDescription({ _pyrit_temperature: { resolved_temperature: value, source } })).toBe(expected)
  })

  it('does not guess the temperature of historical records', () => {
    expect(temperatureDescription({})).toBe('Temperature: not recorded')
    expect(temperatureDescription({ request_temperature: 1 })).toBe('Temperature: not recorded')
  })
})
