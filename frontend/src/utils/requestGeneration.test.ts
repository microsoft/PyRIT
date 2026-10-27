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

  it.each(['', '0'])('replaces both formats of copied options and evidence with selection %s', (input: string) => {
    const piece: MessagePieceRequest = {
      data_type: 'text', original_value: 'hello',
      prompt_metadata: {
        request_temperature: 1.5, _pyrit_temperature: { source: 'request' },
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
    const legacy = {
      _pyrit_temperature: {
        requested_temperature: requested, resolved_temperature: value, source,
        request_piece_ids: ['request-id'],
      },
    }
    expect(temperatureDescription(current)).toBe(expected)
    expect(temperatureDescription(legacy)).toBe(expected)
    expect(temperatureDescription({ ...current, ...legacy })).toBe(expected)
  })

  it('does not guess the temperature of historical records', () => {
    expect(temperatureDescription({})).toBe('Temperature: not recorded')
    expect(temperatureDescription({ request_temperature: 1 })).toBe('Temperature: not recorded')
    expect(temperatureDescription({ request_generation_config: { temperature: 1 } })).toBe('Temperature: not recorded')
    expect(temperatureDescription({ _pyrit_generation: null, _pyrit_temperature: null })).toBe('Temperature: not recorded')
  })

  it.each([
    { requested_temperature: 0.5 }, { resolved_temperature: 0.5 },
    { source: 'target_default' }, { request_piece_ids: ['different-id'] },
  ])('does not conceal contradictory evidence %j', (change: Record<string, unknown>) => {
    expect(temperatureDescription({
      _pyrit_generation: {
        temperature: { requested: 0, resolved: 0, source: 'request' },
        request_piece_ids: ['request-id'],
      },
      _pyrit_temperature: {
        requested_temperature: 0, resolved_temperature: 0, source: 'request',
        request_piece_ids: ['request-id'], ...change,
      },
    })).toBe('Temperature: invalid record')
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
  ])('shows invalid canonical evidence rather than falling back for %j', (value: unknown) => {
    const legacy = { requested_temperature: 0, resolved_temperature: 0, source: 'request' }
    expect(temperatureDescription({ _pyrit_generation: value })).toBe('Temperature: invalid record')
    expect(temperatureDescription({
      _pyrit_generation: value, _pyrit_temperature: legacy,
    })).toBe('Temperature: invalid record')
  })

  it('does not hide malformed legacy evidence behind a valid canonical record', () => {
    expect(temperatureDescription({
      _pyrit_generation: { temperature: { requested: 0, resolved: 0, source: 'request' } },
      _pyrit_temperature: {},
    })).toBe('Temperature: invalid record')
  })
})
