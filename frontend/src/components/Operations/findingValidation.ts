import { groupApiFieldErrors, type ApiError, type GroupedApiFieldErrors } from '@/services/errors'

/** Controls in the finding form that can carry server validation feedback. */
export type FindingFormField =
  | 'title'
  | 'description'
  | 'severity'
  | 'severity_other'
  | 'harm_type'
  | 'harm_type_other'
  | 'attack_result_id'

/** Every field the form knows how to label and validate. */
export const FINDING_FORM_FIELDS: readonly FindingFormField[] = [
  'title',
  'description',
  'severity',
  'severity_other',
  'harm_type',
  'harm_type_other',
  'attack_result_id',
]

/** Visible labels, used by the error summary at the top of the form. */
export const FINDING_FIELD_LABELS: Readonly<Record<FindingFormField, string>> = {
  title: 'Title',
  description: 'Description',
  severity: 'Severity',
  severity_other: 'Other severity',
  harm_type: 'Harm-type',
  harm_type_other: 'Other harm-type',
  attack_result_id: 'Evidence attack ID',
}

/** Backend names that do not exactly match the form control names. */
const FIELD_ALIASES: Readonly<Record<string, FindingFormField>> = {
  attack_id: 'attack_result_id',
  attackId: 'attack_result_id',
  evidence_attack_id: 'attack_result_id',
  severityOther: 'severity_other',
  harmType: 'harm_type',
  harmTypeOther: 'harm_type_other',
}

/**
 * Map a failed save's RFC 7807 field errors onto the fields currently rendered.
 *
 * Passing only visible fields matters: an error for a hidden control (for
 * example `severity_other` while a preset severity is selected) is returned in
 * `unmatched` and shown with its original field path in the error summary
 * instead of blocking Save behind an invisible message.
 */
export function findingFieldErrors(
  error: ApiError,
  visibleFields: readonly FindingFormField[],
): GroupedApiFieldErrors<FindingFormField> {
  return groupApiFieldErrors(error, visibleFields, FIELD_ALIASES)
}
