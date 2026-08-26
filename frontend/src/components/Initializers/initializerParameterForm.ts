import type { Parameter } from '@/types'

/** The control rendered for a parameter, derived from its declared metadata. */
export type ParameterControlKind = 'boolean' | 'select' | 'multiselect' | 'list' | 'number' | 'text'

/** Form state value for a single parameter. Multiselect holds the selected choices; everything else is a raw string. */
export type ParameterFormValue = string | string[]

export function getParameterControlKind(param: Parameter): ParameterControlKind {
  if (param.type_name === 'bool') {
    return 'boolean'
  }
  const hasChoices = (param.choices?.length ?? 0) > 0
  if (param.is_list && hasChoices) {
    return 'multiselect'
  }
  if (hasChoices) {
    return 'select'
  }
  if (param.is_list) {
    return 'list'
  }
  if (param.type_name === 'int' || param.type_name === 'float') {
    return 'number'
  }
  return 'text'
}

function parseListValue(raw: string): string[] {
  return raw
    .split(',')
    .map((entry) => entry.trim())
    .filter((entry) => entry.length > 0)
}

function initialBooleanValue(param: Parameter, initial: unknown): string {
  if (initial != null) {
    return String(initial).toLowerCase() === 'true' ? 'true' : 'false'
  }
  if (param.default != null) {
    return param.default.toLowerCase() === 'true' ? 'true' : 'false'
  }
  return 'false'
}

export function getInitialFormValues(
  params: Parameter[],
  initialParameters?: Record<string, unknown> | null,
): Record<string, ParameterFormValue> {
  const values: Record<string, ParameterFormValue> = {}
  for (const param of params) {
    const initial = initialParameters?.[param.name]
    switch (getParameterControlKind(param)) {
      case 'boolean':
        values[param.name] = initialBooleanValue(param, initial)
        break
      case 'multiselect':
        values[param.name] = Array.isArray(initial) ? initial.map((entry) => String(entry)) : []
        break
      case 'list':
        values[param.name] = Array.isArray(initial)
          ? initial.map((entry) => String(entry)).join(', ')
          : initial != null
            ? String(initial)
            : ''
        break
      default:
        values[param.name] = initial != null ? String(initial) : ''
        break
    }
  }
  return values
}

export type BuildParametersResult =
  | { ok: true; parameters: Record<string, unknown> | null }
  | { ok: false; error: string }

export function buildParametersFromForm(
  params: Parameter[],
  values: Record<string, ParameterFormValue>,
): BuildParametersResult {
  const parameters: Record<string, unknown> = {}

  for (const param of params) {
    const value = values[param.name]
    const kind = getParameterControlKind(param)

    if (kind === 'boolean') {
      parameters[param.name] = value === 'true'
      continue
    }

    if (kind === 'multiselect') {
      const selected = Array.isArray(value) ? value : []
      const invalid = selected.find((entry) => !(param.choices ?? []).includes(entry))
      if (invalid != null) {
        return { ok: false, error: `${param.name}: "${invalid}" is not an allowed value.` }
      }
      if (selected.length === 0) {
        if (param.required) {
          return { ok: false, error: `${param.name} is required.` }
        }
        continue
      }
      parameters[param.name] = selected
      continue
    }

    const raw = typeof value === 'string' ? value.trim() : ''

    if (kind === 'list') {
      const entries = parseListValue(raw)
      if (entries.length === 0) {
        if (param.required) {
          return { ok: false, error: `${param.name} is required.` }
        }
        continue
      }
      parameters[param.name] = entries
      continue
    }

    if (raw.length === 0) {
      if (param.required) {
        return { ok: false, error: `${param.name} is required.` }
      }
      continue
    }

    if (kind === 'select') {
      if (!(param.choices ?? []).includes(raw)) {
        return { ok: false, error: `${param.name}: "${raw}" is not an allowed value.` }
      }
      parameters[param.name] = raw
      continue
    }

    if (kind === 'number') {
      const parsed = Number(raw)
      if (!Number.isFinite(parsed)) {
        return { ok: false, error: `${param.name} must be a number.` }
      }
      if (param.type_name === 'int' && !Number.isInteger(parsed)) {
        return { ok: false, error: `${param.name} must be an integer.` }
      }
      parameters[param.name] = parsed
      continue
    }

    parameters[param.name] = raw
  }

  return { ok: true, parameters: Object.keys(parameters).length > 0 ? parameters : null }
}
