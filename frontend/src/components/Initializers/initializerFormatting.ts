import type { RegisteredInitializer } from '@/types'

export function formatInitializerParameters(parameters?: Record<string, unknown> | null): string {
  return JSON.stringify(parameters ?? {}, null, 2)
}

export function formatSupportedParameterSummary(initializer: RegisteredInitializer): string[] {
  if (initializer.supported_parameters.length === 0) {
    return ['No declared parameters.']
  }

  return initializer.supported_parameters.map((parameter) => {
    const requiredLabel = parameter.required ? 'required' : 'optional'
    return `${parameter.name} (${parameter.type_name}, ${requiredLabel})`
  })
}
