import type { RegisteredInitializer } from '@/types'

import { formatInitializerParameters, formatSupportedParameterSummary } from './initializerFormatting'

describe('formatInitializerParameters', () => {
  it('renders an empty object for null or undefined parameters', () => {
    expect(formatInitializerParameters(null)).toBe('{}')
    expect(formatInitializerParameters(undefined)).toBe('{}')
  })

  it('pretty-prints the provided parameters as indented JSON', () => {
    expect(formatInitializerParameters({ days: 7, tags: ['a', 'b'] })).toBe(
      JSON.stringify({ days: 7, tags: ['a', 'b'] }, null, 2),
    )
  })
})

describe('formatSupportedParameterSummary', () => {
  const baseInitializer: RegisteredInitializer = {
    initializer_name: 'refresh_datasets',
    initializer_type: 'DatasetInitializer',
    description: 'Refreshes datasets.',
    required_env_vars: [],
    supported_parameters: [],
  }

  it('reports when an initializer declares no parameters', () => {
    expect(formatSupportedParameterSummary(baseInitializer)).toEqual(['No declared parameters.'])
  })

  it('summarizes each parameter with its type and required/optional label', () => {
    const initializer: RegisteredInitializer = {
      ...baseInitializer,
      supported_parameters: [
        { name: 'days', type_name: 'int', required: true, default: null, choices: null, is_list: false },
        {
          name: 'dataset_names',
          type_name: 'list[str]',
          required: false,
          default: null,
          choices: null,
          is_list: true,
        },
      ],
    }

    expect(formatSupportedParameterSummary(initializer)).toEqual([
      'days (int, required)',
      'dataset_names (list[str], optional)',
    ])
  })
})
