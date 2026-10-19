import { findingSeverityColor, findingSeverityLabel, FINDING_SEVERITY_LABELS } from './findingSeverity'
import type { FindingSeverity } from '@/types'

it.each<[FindingSeverity, string, string]>([
  ['critical', 'Critical', 'danger'],
  ['important', 'Important', 'warning'],
  ['moderate', 'Moderate', 'warning'],
  ['low', 'Low', 'informative'],
  ['informational', 'Informational', 'subtle'],
  ['other', 'Other', 'subtle'],
])('uses the Operations label and badge color for %s', (severity, label, color) => {
  expect(FINDING_SEVERITY_LABELS[severity]).toBe(label)
  expect(findingSeverityColor(severity)).toBe(color)
})

it('shows the custom assessment as a neutral badge instead of assigning its text a preset priority', () => {
  expect(findingSeverityLabel({ severity: 'other', severity_other: 'Critical' })).toBe('Critical')
  expect(findingSeverityColor('other')).toBe('subtle')
  expect(findingSeverityLabel({ severity: 'low' })).toBe('Low')
})
