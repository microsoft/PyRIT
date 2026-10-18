import { makeTarget } from '@/test-utils/targetFixtures'
import type { TargetCapabilities, TargetInstance } from '../../types'
import {
  DEFAULT_TARGET_FILTERS,
  activeTargetFilters,
  getTargetFilterOptions,
  isSameTargetFilters,
  targetMatchesFilters,
} from './targetFilters'

function makeCapabilities(
  inputs: string[],
  outputs: string[],
  flags: Partial<TargetCapabilities> = {},
): TargetCapabilities {
  return {
    supports_multi_turn: false,
    supports_json_schema: false,
    supports_json_output: false,
    supports_system_prompt: false,
    supported_input_modalities: inputs,
    supported_output_modalities: outputs,
    ...flags,
  }
}

function target(
  name: string,
  type: string,
  capabilities: TargetCapabilities | null,
): TargetInstance {
  return makeTarget({ target_registry_name: name, target_type: type, capabilities })
}

const imageChat = target('image_chat', 'OpenAIChatTarget', makeCapabilities(['text', 'image_path'], ['text'], {
  supports_json_schema: true,
  supports_system_prompt: true,
}))
const audioChat = target('audio_chat', 'RealtimeTarget', makeCapabilities(['text', 'audio_path'], ['text', 'audio_path'], {
  supports_system_prompt: true,
}))
const speech = target('speech', 'OpenAITTSTarget', makeCapabilities(['text'], ['audio_path']))
const bare = target('bare', 'TextTarget', null)

describe('getTargetFilterOptions', () => {
  it('should list each filter in display order with display names', () => {
    const options = getTargetFilterOptions([imageChat, audioChat, speech])

    expect(options.types.map((option) => option.value)).toEqual(['OpenAIChatTarget', 'OpenAITTSTarget', 'RealtimeTarget'])
    expect(options.inputs).toEqual([
      { value: 'text', label: 'Text' },
      { value: 'image_path', label: 'Image' },
      { value: 'audio_path', label: 'Audio' },
    ])
    expect(options.outputs.map((option) => option.label)).toEqual(['Text', 'Audio'])
    expect(options.capabilities).toEqual([
      { value: 'supports_json_schema', label: 'JSON Schema' },
      { value: 'supports_system_prompt', label: 'System Prompt' },
    ])
  })

  it('should list unknown modalities last in alphabetical order', () => {
    const options = getTargetFilterOptions([
      target('first', 'OpenAIChatTarget', makeCapabilities(['zeta_path', 'text'], ['text'])),
      target('second', 'OpenAIChatTarget', makeCapabilities(['alpha_path', 'image_path'], ['text'])),
    ])

    expect(options.inputs.map((option) => option.label)).toEqual(['Text', 'Image', 'alpha_path', 'zeta_path'])
  })

  it('should offer nothing for a filter that cannot narrow the targets', () => {
    const sameInputs = [
      target('first', 'OpenAIChatTarget', makeCapabilities(['text', 'image_path'], ['text'])),
      target('second', 'OpenAIChatTarget', makeCapabilities(['text', 'image_path'], ['audio_path'])),
    ]

    const options = getTargetFilterOptions(sameInputs)

    expect(options.types).toEqual([])
    expect(options.inputs).toEqual([])
    expect(options.outputs.map((option) => option.value)).toEqual(['text', 'audio_path'])
    expect(options.capabilities).toEqual([])
  })

  it('should only count capabilities that are explicitly supported', () => {
    const options = getTargetFilterOptions([imageChat, bare])

    expect(options.capabilities.map((option) => option.value)).toEqual(['supports_json_schema', 'supports_system_prompt'])
  })
})

describe('activeTargetFilters', () => {
  it('should drop selections the options no longer offer', () => {
    const options = getTargetFilterOptions([imageChat, speech])
    const filters = { ...DEFAULT_TARGET_FILTERS, inputs: ['image_path', 'audio_path'], types: ['RealtimeTarget'] }

    expect(activeTargetFilters(filters, options)).toEqual({ ...DEFAULT_TARGET_FILTERS, inputs: ['image_path'] })
  })
})

describe('isSameTargetFilters', () => {
  it('should compare every filter value in order', () => {
    const filters = { ...DEFAULT_TARGET_FILTERS, inputs: ['text', 'image_path'] }

    expect(isSameTargetFilters(filters, { ...filters, inputs: ['text', 'image_path'] })).toBe(true)
    expect(isSameTargetFilters(filters, { ...filters, inputs: ['text'] })).toBe(false)
    expect(isSameTargetFilters(filters, { ...filters, inputs: ['image_path', 'text'] })).toBe(false)
  })
})

describe('targetMatchesFilters', () => {
  const matching = (filters: Partial<typeof DEFAULT_TARGET_FILTERS>): string[] =>
    [imageChat, audioChat, speech, bare]
      .filter((candidate) => targetMatchesFilters(candidate, { ...DEFAULT_TARGET_FILTERS, ...filters }))
      .map((candidate) => candidate.target_registry_name)

  it('should match every target when no filter is set', () => {
    expect(matching({})).toEqual(['image_chat', 'audio_chat', 'speech', 'bare'])
  })

  it('should match any selected type, input, or output', () => {
    expect(matching({ types: ['OpenAIChatTarget', 'OpenAITTSTarget'] })).toEqual(['image_chat', 'speech'])
    expect(matching({ inputs: ['image_path', 'audio_path'] })).toEqual(['image_chat', 'audio_chat'])
    expect(matching({ outputs: ['audio_path'] })).toEqual(['audio_chat', 'speech'])
  })

  it('should require every selected capability', () => {
    expect(matching({ capabilities: ['supports_system_prompt'] })).toEqual(['image_chat', 'audio_chat'])
    expect(matching({ capabilities: ['supports_system_prompt', 'supports_json_schema'] })).toEqual(['image_chat'])
  })

  it('should combine filters', () => {
    expect(matching({ inputs: ['text'], outputs: ['audio_path'], capabilities: ['supports_system_prompt'] }))
      .toEqual(['audio_chat'])
  })
})
