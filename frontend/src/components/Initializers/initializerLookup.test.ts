import type { RegisteredInitializer } from '@/types'

import { resolveRegisteredInitializer } from './initializerLookup'

const registered: RegisteredInitializer[] = [
  {
    initializer_name: 'target',
    initializer_type: 'TargetInitializer',
    description: 'Registers targets.',
    required_env_vars: ['AZURE_OPENAI_ENDPOINT'],
    supported_parameters: [],
  },
  {
    initializer_name: 'scorer',
    initializer_type: 'ScorerInitializer',
    description: 'Registers scorers.',
    required_env_vars: [],
    supported_parameters: [],
  },
]

describe('resolveRegisteredInitializer', () => {
  it('returns the matching catalog entry by name', () => {
    const result = resolveRegisteredInitializer('scorer', registered)

    expect(result).toBe(registered[1])
  })

  it('returns an "no longer registered" placeholder when the name is unknown', () => {
    const result = resolveRegisteredInitializer('ghost', registered)

    expect(result).toEqual({
      initializer_name: 'ghost',
      initializer_type: 'UnknownInitializer',
      description: 'Initializer is no longer registered.',
      required_env_vars: [],
      supported_parameters: [],
    })
  })

  it('returns a placeholder when the catalog is empty', () => {
    const result = resolveRegisteredInitializer('target', [])

    expect(result.initializer_name).toBe('target')
    expect(result.initializer_type).toBe('UnknownInitializer')
  })
})
