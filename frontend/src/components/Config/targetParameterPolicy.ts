import { isJsonObjectParameter } from '@/components/Parameters/parameterForm'
import type { Parameter, TargetTypeEntry } from '@/types'

export type TargetParameterOwner = 'authentication' | 'connection' | 'round_robin' | 'unsupported'

export interface TargetParameterPolicy {
  readonly owner: TargetParameterOwner
  readonly reason?: string
}

const TARGET_PARAMETER_POLICIES: Record<string, TargetParameterPolicy> = {
  api_key: { owner: 'authentication' },
  endpoint: { owner: 'connection' },
  model_name: { owner: 'connection' },
  underlying_model: { owner: 'connection' },
  underlying_model_name: { owner: 'connection' },
  custom_functions: {
    owner: 'unsupported',
    reason: 'Python callables cannot be configured in CopyRIT. Configure this target through the Python framework to use custom functions.',
  },
}

const SCALAR_PARAMETER_TYPES = new Set(['bool', 'float', 'int', 'str'])

export function getTargetParameterPolicy(
  targetType: string,
  parameterName: string,
): TargetParameterPolicy | undefined {
  if (targetType === 'RoundRobinTarget' && (parameterName === 'targets' || parameterName === 'weights')) {
    return { owner: 'round_robin' }
  }
  return TARGET_PARAMETER_POLICIES[parameterName]
}

export function isMetadataDrivenTargetParameter(targetType: string, parameter: Parameter): boolean {
  if (
    getTargetParameterPolicy(targetType, parameter.name)
    || parameter.reference_type
    || parameter.variants
  ) {
    return false
  }
  if (parameter.choices?.length || isJsonObjectParameter(parameter)) {
    return true
  }
  if (parameter.is_list) {
    const elementType = /^list\[(.+)\]$/.exec(parameter.type_name)?.[1]
    return elementType !== undefined && SCALAR_PARAMETER_TYPES.has(elementType)
  }
  return SCALAR_PARAMETER_TYPES.has(parameter.type_name)
}

export function canConfigureRequiredTargetParameter(targetType: string, parameter: Parameter): boolean {
  const policy = getTargetParameterPolicy(targetType, parameter.name)
  return (policy !== undefined && policy.owner !== 'unsupported')
    || isMetadataDrivenTargetParameter(targetType, parameter)
}

export function canConfigureTargetType(entry: TargetTypeEntry): boolean {
  return entry.parameters.every(
    (parameter) => !parameter.required
      || canConfigureRequiredTargetParameter(entry.target_type, parameter),
  )
}
