import type { ParameterFormValue } from '@/components/Parameters/parameterForm'
import type { ScenarioRunEstimate, ScenarioRunEstimateState } from '@/types'

function formatParameterPreview(value: ParameterFormValue | undefined): string {
  if (Array.isArray(value)) {
    return value.length > 0 ? value.join(', ') : 'Not set'
  }
  if (typeof value === 'object') {
    return value.type || 'Not set'
  }
  return value?.trim() || 'Not set'
}

export function formatEffectiveParameterPreview(
  parameterName: string,
  value: ParameterFormValue | undefined,
  estimate: ScenarioRunEstimate | undefined,
): string {
  const configuredValue = formatParameterPreview(value)
  if (configuredValue !== 'Not set') {
    return configuredValue
  }
  const effectiveValue = estimate?.effectiveParameters[parameterName]
  if (Array.isArray(effectiveValue)) {
    return effectiveValue.length > 0 ? effectiveValue.join(', ') : 'Not set'
  }
  return effectiveValue?.toString() ?? 'Not set'
}

export function estimateFromState(state: ScenarioRunEstimateState): ScenarioRunEstimate | undefined {
  switch (state.status) {
    case 'available':
    case 'conditional':
    case 'refreshing':
    case 'stale':
      return state.estimate
    default:
      return undefined
  }
}

export function formatAtomicAttackCount(state: ScenarioRunEstimateState): string {
  const estimate = estimateFromState(state)
  if (!estimate) {
    return state.status === 'loading' ? 'Calculating...' : 'Unknown'
  }
  const prefix = estimate.approximate ? 'About ' : ''
  if (estimate.total !== null) {
    return `${estimate.approximate ? 'Up to ' : ''}${estimate.total.toLocaleString()}`
  }
  if (estimate.minimum != null && estimate.maximum != null) {
    return estimate.minimum === estimate.maximum
      ? `${prefix}${estimate.minimum.toLocaleString()}`
      : `${prefix}${estimate.minimum.toLocaleString()}-${estimate.maximum.toLocaleString()}`
  }
  if (estimate.minimum != null) {
    return `At least ${prefix.toLowerCase()}${estimate.minimum.toLocaleString()}`
  }
  if (estimate.maximum != null) {
    return `Up to ${estimate.maximum.toLocaleString()}`
  }
  return 'Varies'
}

export function estimateNotes(state: ScenarioRunEstimateState): string | null {
  const estimate = estimateFromState(state)
  if (!estimate) {
    return state.status === 'unavailable' ? state.note ?? state.label : null
  }
  const notes = [
    estimate.note,
    ...estimate.components.map((component) => component.note),
  ].filter((note): note is string => Boolean(note))
  return notes.length > 0 ? notes.join('\n\n') : null
}
