import type { MessagePieceRequest } from '@/types'
import { temperatureDescription, withRequestTemperature } from './requestGeneration'

describe('request temperature', () => {
  it.each(['0', '0.8', '2'])('attaches numeric %s to only one piece', (input: string) => {
    const pieces: MessagePieceRequest[] = [
      { data_type: 'text', original_value: 'hello' },
      { data_type: 'image_path', original_value: 'image.png', prompt_metadata: { video_id: 'keep' } },
    ]
    const result = withRequestTemperature(pieces, input)
    expect(result[0].prompt_metadata).toEqual({ request_generation_config: { temperature: Number(input) } })
    expect(result[1].prompt_metadata).toEqual({ video_id: 'keep' })
    expect(pieces[0].prompt_metadata).toBeUndefined()
  })

  it.each(['', '0'])('replaces copied options and evidence with selection %s', (input: string) => {
    const piece: MessagePieceRequest = {
      data_type: 'text', original_value: 'hello',
      prompt_metadata: {
        request_generation_config: { temperature: 1.7 }, _pyrit_generation: { temperature: { source: 'request' } },
        video_id: 'keep',
      },
    }
    const original = JSON.parse(JSON.stringify(piece))
    const result = withRequestTemperature([piece, piece], input)
    expect(result[0].prompt_metadata).toEqual({
      video_id: 'keep', ...(input ? { request_generation_config: { temperature: Number(input) } } : {}),
    })
    expect(result[1].prompt_metadata).toEqual({ video_id: 'keep' })
    expect(piece).toEqual(original)
  })

  it.each(['NaN', 'Infinity', '-0.1', '2.1', 'not a number'])('rejects %s', (input: string) => {
    expect(() => withRequestTemperature([], input)).toThrow(/finite number between 0 and 2/)
  })

  it.each([
    [0, 'request', 'Temperature: 0 (request override)'],
    [0.2, 'target_default', 'Temperature: 0.2 (target default)'],
    [null, 'provider_default', 'Temperature: provider default (unknown)'],
  ])('describes saved %s from %s', (value, source, expected) => {
    const requested = source === 'request' ? value : null
    const current = {
      _pyrit_generation: {
        temperature: { requested, resolved: value, source },
        request_piece_ids: ['request-id'],
      },
    }
    expect(temperatureDescription(current)).toBe(expected)
  })

  it('does not guess the temperature of historical records', () => {
    expect(temperatureDescription({})).toBe('Temperature: not recorded')
    expect(temperatureDescription({ request_temperature: 1 })).toBe('Temperature: not recorded')
    expect(temperatureDescription({ request_generation_config: { temperature: 1 } })).toBe('Temperature: not recorded')
    expect(temperatureDescription({ _pyrit_generation: null })).toBe('Temperature: not recorded')
    expect(temperatureDescription({
      _pyrit_temperature: { requested_temperature: 0, resolved_temperature: 0, source: 'request' },
    })).toBe('Temperature: not recorded')
  })

  it.each([
    false, 0, '', [], {},
    { temperature: {} },
    { temperature: { requested: true, resolved: 0, source: 'request' } },
    { temperature: { requested: 0, resolved: '0', source: 'request' } },
    { temperature: { requested: 0, resolved: Infinity, source: 'request' } },
    { temperature: { requested: null, resolved: 0, source: 'unknown' } },
    { temperature: { requested: 0, resolved: 0, source: 'request' }, request_piece_ids: [1] },
    { temperature: { requested: 0, resolved: 0, source: 'request' }, request_piece_ids: null },
  ])('shows invalid evidence for %j', (value: unknown) => {
    expect(temperatureDescription({ _pyrit_generation: value })).toBe('Temperature: invalid record')
  })
})
