import type { BadgeProps } from '@fluentui/react-components'

import type { FindingField, FindingFieldOption, FindingFields, FindingSeverityColor } from '@/types'

export type BadgeColor = NonNullable<BadgeProps['color']>

export const FLUENT_BADGE_COLOR: Record<FindingSeverityColor, BadgeColor> = {
  red: 'danger',
  orange: 'severe',
  yellow: 'warning',
  green: 'success',
  blue: 'brand',
  gray: 'informative',
  dark: 'important',
  neutral: 'subtle',
}

/** The key under which the severity slot's own value travels with the other values. */
export const SEVERITY_KEY = 'severity'

export interface FindingValueRow {
  key: string
  label: string
  /** The option label of a matching choice value, otherwise the stored text. */
  value: string
  multiline: boolean
}

export interface RetiredValue {
  key: string
  value: string
}

export interface SeverityCountBadge {
  key: string
  label: string
  color: BadgeColor
  count: number
  /** The `value: count` of each unmatched severity, on the Other badge only. */
  tooltip: string | null
}

const OTHER_SEVERITY_BADGE: Pick<SeverityCountBadge, 'key' | 'label' | 'color'> = {
  key: 'other',
  label: 'Other',
  color: FLUENT_BADGE_COLOR.neutral,
}

/** The value, or null when it is missing or blank. */
export function filled(value: string | undefined): string | null {
  return value !== undefined && value.trim() !== '' ? value : null
}

/** The value stored under a configured key, ignoring `Object.prototype` members such as `constructor`. */
export function ownValue<T>(record: Readonly<Record<string, T>>, key: string): T | undefined {
  return Object.prototype.hasOwnProperty.call(record, key) ? record[key] : undefined
}

export function findOption(options: FindingFieldOption[], key: string | null): FindingFieldOption | undefined {
  return options.find((option: FindingFieldOption) => option.key === key)
}

function allFields(fields: FindingFields): FindingField[] {
  return [fields.severity, ...fields.fields]
}

/** The severity's option label and badge color, or the stored text on a neutral badge when no option matches. */
export function severityBadge(fields: FindingFields, severity: string): { label: string; color: BadgeColor } {
  const match = findOption(fields.severity.options, severity)
  if (match === undefined) return { label: severity, color: FLUENT_BADGE_COLOR.neutral }
  return { label: match.label, color: FLUENT_BADGE_COLOR[ownValue(fields.severity.colors, severity) ?? 'neutral'] }
}

/** The field itself, then each of its levels: one entry per dropdown of a choice field. */
export function choiceLevels(field: FindingField): Array<{ key: string; label: string }> {
  return [
    { key: field.key, label: field.label },
    ...field.levels.map(([key, label]: [string, string]) => ({ key, label })),
  ]
}

/**
 * The options a choice field's dropdown offers under the values chosen above it.
 * @param values - Values by key. The severity slot's own value goes under `severity`.
 * @param depth - 0 for the field's own dropdown, 1 for its first level, and so on.
 * @returns No options when a value above is missing or isn't an option.
 */
export function levelOptions(field: FindingField, values: Record<string, string>, depth: number): FindingFieldOption[] {
  let options = field.options
  for (const level of choiceLevels(field).slice(0, depth)) {
    const match = findOption(options, filled(ownValue(values, level.key)))
    if (match === undefined) return []
    options = match.children
  }
  return options
}

/** The key of every field and level, except `severity`, which isn't stored in `field_values`. */
export function fieldKeys(fields: FindingFields): Set<string> {
  const keys = new Set<string>()
  for (const field of allFields(fields)) {
    for (const level of choiceLevels(field)) keys.add(level.key)
  }
  keys.delete(SEVERITY_KEY)
  return keys
}

/** Sets apart the stored values whose keys the active fields no longer define, keeping their stored order. */
export function splitRetiredValues(
  fields: FindingFields,
  fieldValues: Record<string, string>,
): { current: Record<string, string>; retired: RetiredValue[] } {
  const known = fieldKeys(fields)
  const current: Record<string, string> = {}
  const retired: RetiredValue[] = []
  for (const [key, value] of Object.entries(fieldValues)) {
    if (known.has(key)) current[key] = value
    else retired.push({ key, value })
  }
  return { current, retired }
}

function displayValue(field: FindingField, values: Record<string, string>, depth: number, value: string): string {
  if (field.type !== 'choice') return value
  return findOption(levelOptions(field, values, depth), value)?.label ?? value
}

/**
 * Lists a finding's filled values in configured order, and its retired values in stored order.
 * The severity itself isn't a row; it is the parent path of the severity slot's levels.
 */
export function describeFindingValues(
  fields: FindingFields,
  severity: string,
  fieldValues: Record<string, string>,
): { rows: FindingValueRow[]; retired: RetiredValue[] } {
  const values: Record<string, string> = { ...fieldValues, [SEVERITY_KEY]: severity }
  const rows: FindingValueRow[] = []
  for (const field of allFields(fields)) {
    for (const [depth, level] of choiceLevels(field).entries()) {
      if (field === fields.severity && depth === 0) continue
      const value = filled(ownValue(fieldValues, level.key))
      if (value === null) continue
      rows.push({
        key: level.key,
        label: level.label,
        value: displayValue(field, values, depth, value),
        multiline: field.type === 'multiline',
      })
    }
  }
  return { rows, retired: splitRetiredValues(fields, fieldValues).retired }
}

function firstMissingLevel(field: FindingField, values: Record<string, string>): string | null {
  for (const [depth, level] of choiceLevels(field).entries()) {
    if (levelOptions(field, values, depth).length === 0) return null
    if (filled(ownValue(values, level.key)) === null) return level.label
  }
  return null
}

/**
 * The label of the first missing required value, as the backend checks them: the severity always,
 * a required text field when blank, and each level of a required choice that offers options under
 * the chosen parent.
 */
export function firstMissingRequired(
  fields: FindingFields,
  severity: string,
  fieldValues: Record<string, string>,
): string | null {
  const values: Record<string, string> = { ...fieldValues, [SEVERITY_KEY]: severity }
  for (const field of allFields(fields)) {
    if (field !== fields.severity && !field.required) continue
    if (field.type === 'choice') {
      const missing = firstMissingLevel(field, values)
      if (missing !== null) return missing
    } else if (filled(ownValue(values, field.key)) === null) {
      return field.label
    }
  }
  return null
}

/** One badge per counted severity option, in option order, then one Other badge for every unmatched severity. */
export function severityCountBadges(fields: FindingFields, counts: Record<string, number>): SeverityCountBadge[] {
  const badges: SeverityCountBadge[] = []
  for (const option of fields.severity.options) {
    const count = ownValue(counts, option.key) ?? 0
    if (count > 0) badges.push({ key: option.key, ...severityBadge(fields, option.key), count, tooltip: null })
  }
  const optionKeys = new Set(fields.severity.options.map((option: FindingFieldOption) => option.key))
  const unmatched = Object.entries(counts)
    .filter(([severity, count]: [string, number]) => count > 0 && !optionKeys.has(severity))
    .sort(([left]: [string, number], [right]: [string, number]) => left.localeCompare(right))
  if (unmatched.length > 0) {
    badges.push({
      ...OTHER_SEVERITY_BADGE,
      count: unmatched.reduce((total: number, [, count]: [string, number]) => total + count, 0),
      tooltip: unmatched.map(([severity, count]: [string, number]) => `${severity}: ${count}`).join(', '),
    })
  }
  return badges
}
