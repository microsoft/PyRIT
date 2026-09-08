import type { Parameter } from '@/types'

/**
 * Shared parameter-form logic reused by every dynamic parameter form in the
 * app (initializer parameters, scenario-specific parameters, ...). The
 * control kind, form-value shape, default-initialization, and coercion/
 * validation rules all live here so every consumer behaves identically.
 */

/** The control rendered for a parameter, derived from its declared metadata. */
export type ParameterControlKind = 'boolean' | 'select' | 'multiselect' | 'list' | 'number' | 'text'

/**
 * Form state value for a single parameter.
 *
 * A boolean parameter's value is one of `''` (unset — distinct from a
 * chosen `false`), `'true'`, or `'false'`. Everything else is a raw string
 * (scalar / unconstrained list, comma-joined) or a string array
 * (multiselect selections).
 */
export type ParameterFormValue = string | string[]

/** Sentinel form value meaning "the user has not chosen true or false yet". */
export const UNSET_BOOLEAN_VALUE = ''

export interface InitialFormValueOptions {
  /** Populate absent values from the parameter declaration. Defaults to true. */
  prefillDefaults?: boolean
}

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

/**
 * The element type name for a list parameter's declared type (e.g. `'int'`
 * for `'list[int]'`), or the parameter's own `type_name` when it isn't a
 * list. Drives per-element coercion for list/multiselect parameters.
 */
function elementTypeName(param: Parameter): string {
  if (!param.is_list) {
    return param.type_name
  }
  const match = /^list\[(.+)\]$/.exec(param.type_name)
  return match ? match[1] : 'str'
}

function parseListValue(raw: string): string[] {
  return raw
    .split(',')
    .map((entry) => entry.trim())
    .filter((entry) => entry.length > 0)
}

/** Derives the initial tri-state boolean form value: `''` (unset), `'true'`, or `'false'`. */
function initialBooleanValue(source: unknown): string {
  if (source == null) {
    return UNSET_BOOLEAN_VALUE
  }
  return String(source).toLowerCase() === 'true' ? 'true' : 'false'
}

export function getInitialFormValues(
  params: Parameter[],
  initialParameters?: Record<string, unknown> | null,
  options: InitialFormValueOptions = {},
): Record<string, ParameterFormValue> {
  const values: Record<string, ParameterFormValue> = {}
  const prefillDefaults = options.prefillDefaults ?? true
  for (const param of params) {
    const hasInitialValue =
      initialParameters !== null
      && initialParameters !== undefined
      && Object.prototype.hasOwnProperty.call(initialParameters, param.name)
    const source = hasInitialValue
      ? initialParameters[param.name]
      : prefillDefaults
        ? param.default
        : undefined
    switch (getParameterControlKind(param)) {
      case 'boolean':
        values[param.name] = initialBooleanValue(source)
        break
      case 'multiselect': {
        values[param.name] = Array.isArray(source) ? source.map((entry) => String(entry)) : []
        break
      }
      case 'list': {
        values[param.name] = Array.isArray(source)
          ? source.map((entry) => String(entry)).join(', ')
          : source != null
            ? String(source)
            : ''
        break
      }
      default: {
        values[param.name] = source != null ? String(source) : ''
        break
      }
    }
  }
  return values
}

export type BuildParametersResult =
  | { ok: true; parameters: Record<string, unknown> | null }
  | { ok: false; error: string }

type CoerceResult = { ok: true; value: unknown } | { ok: false; error: string }

/** Coerces a single string token to the declared scalar type (`int` / `float` / `bool` / anything else passes through as a string). */
function coerceToken(raw: string, typeName: string, paramName: string): CoerceResult {
  if (typeName === 'int') {
    const parsed = Number(raw)
    if (!Number.isFinite(parsed)) {
      return { ok: false, error: `${paramName} must be a number.` }
    }
    if (!Number.isInteger(parsed)) {
      return { ok: false, error: `${paramName} must be an integer.` }
    }
    return { ok: true, value: parsed }
  }
  if (typeName === 'float') {
    const parsed = Number(raw)
    if (!Number.isFinite(parsed)) {
      return { ok: false, error: `${paramName} must be a number.` }
    }
    return { ok: true, value: parsed }
  }
  if (typeName === 'bool') {
    const normalized = raw.toLowerCase()
    if (normalized === 'true' || normalized === '1' || normalized === 'yes') {
      return { ok: true, value: true }
    }
    if (normalized === 'false' || normalized === '0' || normalized === 'no') {
      return { ok: true, value: false }
    }
    return { ok: false, error: `${paramName} must be true or false.` }
  }
  return { ok: true, value: raw }
}

export function buildParametersFromForm(
  params: Parameter[],
  values: Record<string, ParameterFormValue>,
): BuildParametersResult {
  const parameters: Record<string, unknown> = {}

  for (const param of params) {
    const value = values[param.name]
    const kind = getParameterControlKind(param)

    if (kind === 'boolean') {
      if (value !== 'true' && value !== 'false') {
        if (param.required) {
          return { ok: false, error: `${param.name} is required.` }
        }
        continue
      }
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
      const coercedList: unknown[] = []
      for (const entry of selected) {
        const coerced = coerceToken(entry, elementTypeName(param), param.name)
        if (!coerced.ok) {
          return coerced
        }
        coercedList.push(coerced.value)
      }
      parameters[param.name] = coercedList
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
      const coercedList: unknown[] = []
      for (const entry of entries) {
        const coerced = coerceToken(entry, elementTypeName(param), param.name)
        if (!coerced.ok) {
          return coerced
        }
        coercedList.push(coerced.value)
      }
      parameters[param.name] = coercedList
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
      const coerced = coerceToken(raw, param.type_name, param.name)
      if (!coerced.ok) {
        return coerced
      }
      parameters[param.name] = coerced.value
      continue
    }

    if (kind === 'number') {
      const coerced = coerceToken(raw, param.type_name, param.name)
      if (!coerced.ok) {
        return coerced
      }
      parameters[param.name] = coerced.value
      continue
    }

    parameters[param.name] = raw
  }

  return { ok: true, parameters: Object.keys(parameters).length > 0 ? parameters : null }
}
