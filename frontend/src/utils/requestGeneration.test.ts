import type { MessagePieceRequest } from '@/types'
import { generationDisplay, parseGenerationConfig, withRequestGenerationConfig } from './requestGeneration'

describe('request temperature', () => {
  it.each(['0', '0.8', '2'])('attaches numeric %s to only one piece', (input: string) => {
    const pieces: MessagePieceRequest[] = [
      { data_type: 'text', original_value: 'hello' },
      { data_type: 'image_path', original_value: 'image.png', prompt_metadata: { video_id: 'keep' } },
    ]
    const result = withRequestGenerationConfig(pieces, { temperature: Number(input) })
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
    const result = withRequestGenerationConfig([piece, piece], input ? { temperature: Number(input) } : {})
    expect(result[0].prompt_metadata).toEqual({
      video_id: 'keep', ...(input ? { request_generation_config: { temperature: Number(input) } } : {}),
    })
    expect(result[1].prompt_metadata).toEqual({ video_id: 'keep' })
    expect(piece).toEqual(original)
  })

  it.each(['', ' ', 'NaN', 'Infinity', '-0.1', '2.1', 'not a number'])('rejects %s', (input: string) => {
    expect(() => parseGenerationConfig({ temperature: input }, { supports_temperature: true }))
      .toThrow(/finite number between 0 and 2/)
  })

  it('omits defaults and preserves explicit zero', () => {
    expect(parseGenerationConfig({})).toEqual({})
    expect(parseGenerationConfig({ temperature: '0' }, { supports_temperature: true })).toEqual({ temperature: 0 })
    expect(withRequestGenerationConfig([{ data_type: 'text', original_value: 'hi' }], { temperature: null })[0].prompt_metadata)
      .toBeUndefined()
    expect(() => parseGenerationConfig({ temperature: '0' })).toThrow(/does not support runtime temperature/)
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
    expect(generationDisplay(current).generationDescription).toBe(expected)
    expect(generationDisplay(current).generationDetails).toContain(`Requested override: ${requested ?? 'none'}`)
    expect(generationDisplay(current).generationDetails).toContain(`Sent parameter: ${value ?? 'omitted (provider default unknown)'}`)
  })

  it('does not guess the temperature of historical records', () => {
    expect(generationDisplay({}).generationDescription).toBe('Temperature: not recorded')
    expect(generationDisplay({ request_temperature: 1 }).generationDescription).toBe('Temperature: not recorded')
    expect(generationDisplay({ request_generation_config: { temperature: 1 } }).generationDescription).toBe('Temperature: not recorded')
    expect(generationDisplay({ _pyrit_generation: null }).generationDescription).toBe('Temperature: not recorded')
    expect(generationDisplay({
      _pyrit_temperature: { requested_temperature: 0, resolved_temperature: 0, source: 'request' },
    }).generationDescription).toBe('Temperature: not recorded')
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
    expect(generationDisplay({ _pyrit_generation: value }).generationDescription).toBe('Temperature: invalid record')
  })
})
