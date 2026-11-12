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
