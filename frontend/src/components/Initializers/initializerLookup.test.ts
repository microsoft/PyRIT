import type { RegisteredInitializer } from '@/types'

import { findRegisteredInitializer, initializerFallbackDescription } from './initializerLookup'

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

describe('findRegisteredInitializer', () => {
  it('returns the matching catalog entry by name', () => {
    const result = findRegisteredInitializer('scorer', registered)

    expect(result).toBe(registered[1])
  })

  it('returns undefined when the name is not in a loaded catalog', () => {
    const result = findRegisteredInitializer('ghost', registered)

    expect(result).toBeUndefined()
  })

  it('returns undefined when the catalog is empty', () => {
    const result = findRegisteredInitializer('target', [])

    expect(result).toBeUndefined()
  })
})

describe('initializerFallbackDescription', () => {
  it('reports a definitive unregistration once the catalog has loaded', () => {
    expect(initializerFallbackDescription('loaded')).toBe('Initializer is no longer registered.')
  })

  it('reports a temporary outage when the catalog request failed', () => {
    expect(initializerFallbackDescription('error')).toBe('Catalog metadata temporarily unavailable.')
  })

  it('does not claim unregistration while the catalog is still loading', () => {
    expect(initializerFallbackDescription('loading')).toBe('Catalog metadata temporarily unavailable.')
  })
})
