import type { Parameter } from '@/types'

import {
  buildParametersFromForm,
  getInitialFormValues,
  getParameterControlKind,
  UNSET_BOOLEAN_VALUE,
} from './parameterForm'

function makeParameter(overrides: Partial<Parameter> & { name: string }): Parameter {
  return {
    type_name: 'str',
    required: false,
    default: null,
    choices: null,
    is_list: false,
    description: null,
    ...overrides,
  }
}

describe('getParameterControlKind', () => {
  it('returns boolean for bool parameters', () => {
    expect(getParameterControlKind(makeParameter({ name: 'flag', type_name: 'bool' }))).toBe('boolean')
  })

  it('returns multiselect for a constrained list', () => {
    const param = makeParameter({ name: 'tags', type_name: 'list[str]', is_list: true, choices: ['a', 'b'] })
    expect(getParameterControlKind(param)).toBe('multiselect')
  })

  it('returns select for a constrained scalar', () => {
    const param = makeParameter({ name: 'mode', choices: ['fast', 'slow'] })
    expect(getParameterControlKind(param)).toBe('select')
  })

  it('returns list for an unconstrained list', () => {
    const param = makeParameter({ name: 'names', type_name: 'list[str]', is_list: true })
    expect(getParameterControlKind(param)).toBe('list')
  })

  it('returns number for int and float parameters', () => {
    expect(getParameterControlKind(makeParameter({ name: 'days', type_name: 'int' }))).toBe('number')
    expect(getParameterControlKind(makeParameter({ name: 'ratio', type_name: 'float' }))).toBe('number')
  })

  it('returns text as the default', () => {
    expect(getParameterControlKind(makeParameter({ name: 'label' }))).toBe('text')
  })
})

describe('getInitialFormValues', () => {
  it('derives boolean strings from the provided value, honoring an explicit false', () => {
    const params = [
      makeParameter({ name: 'a', type_name: 'bool' }),
      makeParameter({ name: 'b', type_name: 'bool', default: 'true' }),
      makeParameter({ name: 'c', type_name: 'bool' }),
      makeParameter({ name: 'd', type_name: 'bool' }),
    ]
    const values = getInitialFormValues(params, { a: true, d: false })
    expect(values).toEqual({ a: 'true', b: 'true', c: UNSET_BOOLEAN_VALUE, d: 'false' })
  })

  it('leaves an optional boolean with no initial value or default unset', () => {
    const params = [makeParameter({ name: 'flag', type_name: 'bool' })]
    expect(getInitialFormValues(params)).toEqual({ flag: UNSET_BOOLEAN_VALUE })
  })

  it('derives multiselect arrays and list strings from initial values', () => {
    const params = [
      makeParameter({ name: 'tags', type_name: 'list[str]', is_list: true, choices: ['x', 'y'] }),
      makeParameter({ name: 'names', type_name: 'list[str]', is_list: true }),
    ]
    const values = getInitialFormValues(params, { tags: ['x'], names: ['one', 'two'] })
    expect(values).toEqual({ tags: ['x'], names: 'one, two' })
  })

  it('honors a declared list default when no initial value is provided', () => {
    const params = [
      makeParameter({ name: 'tags', type_name: 'list[str]', is_list: true, choices: ['x', 'y'], default: ['y'] }),
      makeParameter({ name: 'names', type_name: 'list[str]', is_list: true, default: ['a', 'b'] }),
    ]
    expect(getInitialFormValues(params)).toEqual({ tags: ['y'], names: 'a, b' })
  })

  it('stringifies scalar values, honors a declared scalar default, and defaults to empty strings', () => {
    const params = [
      makeParameter({ name: 'days', type_name: 'int' }),
      makeParameter({ name: 'label' }),
      makeParameter({ name: 'ratio', type_name: 'float', default: '1.5' }),
    ]
    expect(getInitialFormValues(params, { days: 7 })).toEqual({ days: '7', label: '', ratio: '1.5' })
  })

  it('preserves explicit null values instead of replacing them with defaults', () => {
    const params = [
      makeParameter({ name: 'flag', type_name: 'bool', default: 'true' }),
      makeParameter({ name: 'days', type_name: 'int', default: '7' }),
    ]
    expect(getInitialFormValues(params, { flag: null, days: null })).toEqual({
      flag: UNSET_BOOLEAN_VALUE,
      days: '',
    })
  })

  it('can leave absent values unset when editing persisted parameters', () => {
    const params = [
      makeParameter({ name: 'flag', type_name: 'bool', default: 'true' }),
      makeParameter({ name: 'days', type_name: 'int', default: '7' }),
    ]
    expect(getInitialFormValues(params, {}, { prefillDefaults: false })).toEqual({
      flag: UNSET_BOOLEAN_VALUE,
      days: '',
    })
  })
})

describe('buildParametersFromForm', () => {
  it('returns null when nothing is provided', () => {
    const params = [makeParameter({ name: 'label' })]
    const result = buildParametersFromForm(params, { label: '  ' })
    expect(result).toEqual({ ok: true, parameters: null })
  })

  it('coerces a valid integer', () => {
    const params = [makeParameter({ name: 'days', type_name: 'int' })]
    const result = buildParametersFromForm(params, { days: '7' })
    expect(result).toEqual({ ok: true, parameters: { days: 7 } })
  })

  it('rejects a non-numeric value', () => {
    const params = [makeParameter({ name: 'days', type_name: 'int' })]
    const result = buildParametersFromForm(params, { days: 'seven' })
    expect(result).toEqual({ ok: false, error: 'days must be a number.' })
  })

  it('rejects a non-integer for an int parameter', () => {
    const params = [makeParameter({ name: 'days', type_name: 'int' })]
    const result = buildParametersFromForm(params, { days: '1.5' })
    expect(result).toEqual({ ok: false, error: 'days must be an integer.' })
  })

  it('coerces a valid float', () => {
    const params = [makeParameter({ name: 'ratio', type_name: 'float' })]
    const result = buildParametersFromForm(params, { ratio: '1.5' })
    expect(result).toEqual({ ok: true, parameters: { ratio: 1.5 } })
  })

  it('splits a comma-separated list', () => {
    const params = [makeParameter({ name: 'names', type_name: 'list[str]', is_list: true })]
    const result = buildParametersFromForm(params, { names: 'a, b ,, c' })
    expect(result).toEqual({ ok: true, parameters: { names: ['a', 'b', 'c'] } })
  })

  it('coerces list elements to the declared element type', () => {
    const params = [makeParameter({ name: 'days', type_name: 'list[int]', is_list: true })]
    const result = buildParametersFromForm(params, { days: '1, 2, 3' })
    expect(result).toEqual({ ok: true, parameters: { days: [1, 2, 3] } })
  })

  it('coerces accepted list[bool] spellings', () => {
    const params = [makeParameter({ name: 'flags', type_name: 'list[bool]', is_list: true })]
    const result = buildParametersFromForm(params, { flags: 'true, 0, yes, no' })
    expect(result).toEqual({ ok: true, parameters: { flags: [true, false, true, false] } })
  })

  it('rejects an invalid list[bool] token', () => {
    const params = [makeParameter({ name: 'flags', type_name: 'list[bool]', is_list: true })]
    const result = buildParametersFromForm(params, { flags: 'true, maybe' })
    expect(result).toEqual({ ok: false, error: 'flags must be true or false.' })
  })

  it('rejects a non-integer list element for a list[int] parameter', () => {
    const params = [makeParameter({ name: 'days', type_name: 'list[int]', is_list: true })]
    const result = buildParametersFromForm(params, { days: '1, x' })
    expect(result).toEqual({ ok: false, error: 'days must be a number.' })
  })

  it('keeps selected multiselect choices', () => {
    const params = [
      makeParameter({ name: 'tags', type_name: 'list[str]', is_list: true, choices: ['a', 'b'] }),
    ]
    const result = buildParametersFromForm(params, { tags: ['a', 'b'] })
    expect(result).toEqual({ ok: true, parameters: { tags: ['a', 'b'] } })
  })

  it('coerces constrained multiselect choices declared as list[int]', () => {
    const params = [
      makeParameter({ name: 'levels', type_name: 'list[int]', is_list: true, choices: ['1', '2', '3'] }),
    ]
    const result = buildParametersFromForm(params, { levels: ['1', '3'] })
    expect(result).toEqual({ ok: true, parameters: { levels: [1, 3] } })
  })

  it('rejects a multiselect value outside the allowed set', () => {
    const params = [
      makeParameter({ name: 'tags', type_name: 'list[str]', is_list: true, choices: ['a', 'b'] }),
    ]
    const result = buildParametersFromForm(params, { tags: ['a', 'c'] })
    expect(result).toEqual({ ok: false, error: 'tags: "c" is not an allowed value.' })
  })

  it('rejects a select value outside the allowed set', () => {
    const params = [makeParameter({ name: 'mode', choices: ['fast', 'slow'] })]
    const result = buildParametersFromForm(params, { mode: 'medium' })
    expect(result).toEqual({ ok: false, error: 'mode: "medium" is not an allowed value.' })
  })

  it('coerces a constrained scalar declared as int (Literal[int]/Enum-of-int)', () => {
    const params = [makeParameter({ name: 'level', type_name: 'int', choices: ['1', '2'] })]
    const result = buildParametersFromForm(params, { level: '2' })
    expect(result).toEqual({ ok: true, parameters: { level: 2 } })
  })

  it('coerces booleans', () => {
    const params = [
      makeParameter({ name: 'on', type_name: 'bool' }),
      makeParameter({ name: 'off', type_name: 'bool' }),
    ]
    const result = buildParametersFromForm(params, { on: 'true', off: 'false' })
    expect(result).toEqual({ ok: true, parameters: { on: true, off: false } })
  })

  it('omits an optional boolean left unset', () => {
    const params = [makeParameter({ name: 'flag', type_name: 'bool' })]
    const result = buildParametersFromForm(params, { flag: UNSET_BOOLEAN_VALUE })
    expect(result).toEqual({ ok: true, parameters: null })
  })

  it('reports a required boolean left unset', () => {
    const params = [makeParameter({ name: 'flag', type_name: 'bool', required: true })]
    const result = buildParametersFromForm(params, { flag: UNSET_BOOLEAN_VALUE })
    expect(result).toEqual({ ok: false, error: 'flag is required.' })
  })

  it('reports a required parameter with no value', () => {
    const params = [makeParameter({ name: 'label', required: true })]
    const result = buildParametersFromForm(params, { label: '' })
    expect(result).toEqual({ ok: false, error: 'label is required.' })
  })

  it('reports a required list with no entries', () => {
    const params = [makeParameter({ name: 'names', type_name: 'list[str]', is_list: true, required: true })]
    const result = buildParametersFromForm(params, { names: '' })
    expect(result).toEqual({ ok: false, error: 'names is required.' })
  })

  it('reports a required multiselect with no selection', () => {
    const params = [
      makeParameter({ name: 'tags', type_name: 'list[str]', is_list: true, choices: ['a'], required: true }),
    ]
    const result = buildParametersFromForm(params, { tags: [] })
    expect(result).toEqual({ ok: false, error: 'tags is required.' })
  })
})
