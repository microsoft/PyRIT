import type { Parameter, TargetTypeEntry } from '@/types'

import {
  canConfigureRequiredTargetParameter,
  canConfigureTargetType,
  getTargetParameterPolicy,
  isMetadataDrivenTargetParameter,
} from './targetParameterPolicy'

function makeParameter(overrides: Partial<Parameter> & { name: string }): Parameter {
  return {
    type_name: 'str',
    required: false,
    default: null,
    choices: null,
    is_list: false,
    variants: null,
    reference_type: null,
    description: null,
    ...overrides,
  }
}

function makeTargetType(
  targetType: string,
  parameters: Parameter[],
): TargetTypeEntry {
  return {
    target_type: targetType,
    parameters,
    supported_auth_modes: ['api_key'],
    description: null,
  }
}

describe('target parameter ownership', () => {
  it.each([
    ['api_key', 'authentication'],
    ['endpoint', 'connection'],
    ['model_name', 'connection'],
    ['underlying_model', 'connection'],
  ] as const)('assigns %s to the %s UI', (name, owner) => {
    expect(getTargetParameterPolicy('OpenAIChatTarget', name)?.owner).toBe(owner)
  })

  it('assigns RoundRobin targets and weights only to the RoundRobin picker', () => {
    expect(getTargetParameterPolicy('RoundRobinTarget', 'targets')?.owner).toBe('round_robin')
    expect(getTargetParameterPolicy('RoundRobinTarget', 'weights')?.owner).toBe('round_robin')
    expect(getTargetParameterPolicy('OpenAIChatTarget', 'weights')).toBeUndefined()
  })

  it('marks custom_functions unsupported with a visible reason', () => {
    const policy = getTargetParameterPolicy('OpenAIResponseTarget', 'custom_functions')
    expect(policy?.owner).toBe('unsupported')
    expect(policy?.reason).toMatch(/Python callables cannot be configured/)
  })
})

describe('metadata-driven target parameters', () => {
  it.each([
    makeParameter({ name: 'temperature', type_name: 'float' }),
    makeParameter({ name: 'seed', type_name: 'int' }),
    makeParameter({ name: 'enabled', type_name: 'bool' }),
    makeParameter({ name: 'mode', type_name: 'CustomEnum', choices: ['a', 'b'] }),
    makeParameter({ name: 'stop', type_name: 'list[str]', is_list: true }),
    makeParameter({ name: 'extra_body_parameters', type_name: 'dict[str, typing.Any]' }),
  ])('renders $name from metadata', (parameter) => {
    expect(isMetadataDrivenTargetParameter('OpenAIChatTarget', parameter)).toBe(true)
  })

  it.each([
    makeParameter({ name: 'callback', type_name: 'Callable' }),
    makeParameter({ name: 'target', reference_type: 'target' }),
    makeParameter({
      name: 'strategy',
      variants: { fixed: [makeParameter({ name: 'count', type_name: 'int' })] },
    }),
    makeParameter({ name: 'mixed', type_name: 'list[str | bytes]', is_list: true }),
  ])('does not claim unsupported parameter $name is renderable', (parameter) => {
    expect(isMetadataDrivenTargetParameter('ExampleTarget', parameter)).toBe(false)
  })
})

describe('target type configurability', () => {
  it('accepts required metadata and specially owned parameters', () => {
    const entry = makeTargetType('RoundRobinTarget', [
      makeParameter({
        name: 'targets',
        type_name: 'list[str]',
        is_list: true,
        required: true,
        reference_type: 'target',
      }),
      makeParameter({ name: 'weights', type_name: 'list[int]', is_list: true }),
    ])
    expect(canConfigureTargetType(entry)).toBe(true)
  })

  it('rejects a target with an unsupported required Python object', () => {
    const page = makeParameter({
      name: 'page',
      type_name: 'Page',
      required: true,
    })
    expect(canConfigureRequiredTargetParameter('PlaywrightTarget', page)).toBe(false)
    expect(canConfigureTargetType(makeTargetType('PlaywrightTarget', [page]))).toBe(false)
  })

  it('allows unsupported optional parameters without claiming to render them', () => {
    const callback = makeParameter({
      name: 'callback',
      type_name: 'Callable',
      required: false,
    })
    expect(canConfigureTargetType(makeTargetType('HTTPTarget', [
      makeParameter({ name: 'http_request', required: true }),
      callback,
    ]))).toBe(true)
    expect(isMetadataDrivenTargetParameter('HTTPTarget', callback)).toBe(false)
  })
})
