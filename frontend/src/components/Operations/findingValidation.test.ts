import { toApiError } from '@/services/errors'

import { FINDING_FIELD_LABELS, FINDING_FORM_FIELDS, findingFieldErrors } from './findingValidation'

function validationError(errors: unknown): ReturnType<typeof toApiError> {
  return toApiError({
    isAxiosError: true,
    response: {
      status: 422,
      data: { detail: 'Request validation failed', errors },
      headers: {}, config: {},
    },
  })
}

describe('findingFieldErrors', () => {
  it('maps the backend evidence field path onto the visible evidence control', () => {
    const grouped = findingFieldErrors(validationError([
      { field: 'body.attack_result_id', message: 'Input should be a valid UUID' },
    ]), FINDING_FORM_FIELDS)

    expect(grouped.fields).toEqual({ attack_result_id: 'Input should be a valid UUID' })
    expect(grouped.unmatched).toEqual([])
    expect(FINDING_FIELD_LABELS.attack_result_id).toBe('Evidence attack ID')
  })

  it('accepts backend aliases such as attackId', () => {
    const grouped = findingFieldErrors(validationError([
      { field: 'body.attackId', message: 'Invalid UUID' },
    ]), FINDING_FORM_FIELDS)

    expect(grouped.fields).toEqual({ attack_result_id: 'Invalid UUID' })
  })

  it('keeps errors for hidden controls in the summary instead of blocking Save', () => {
    const grouped = findingFieldErrors(validationError([
      { field: 'body.severity_other', message: 'Required' },
      { field: 'body.title', message: 'Too short' },
    ]), ['title', 'severity', 'description', 'harm_type'])

    expect(grouped.fields).toEqual({ title: 'Too short' })
    expect(grouped.unmatched).toEqual([{ field: 'body.severity_other', message: 'Required' }])
  })

  it('returns no mapped fields for a non-validation error', () => {
    const grouped = findingFieldErrors(toApiError(new Error('Storage offline')), FINDING_FORM_FIELDS)

    expect(grouped.fields).toEqual({})
    expect(grouped.unmatched).toEqual([])
  })
})
