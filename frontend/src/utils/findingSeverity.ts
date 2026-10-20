import type { FindingCreate, FindingSeverity } from '@/types'

export const FINDING_SEVERITY_LABELS: Record<FindingSeverity, string> = {
  critical: 'Critical', important: 'Important', moderate: 'Moderate',
  low: 'Low', informational: 'Informational', other: 'Other',
}

export function findingSeverityLabel(finding: Pick<FindingCreate, 'severity' | 'severity_other'>): string {
  return finding.severity === 'other' ? finding.severity_other ?? FINDING_SEVERITY_LABELS.other : FINDING_SEVERITY_LABELS[finding.severity]
}

export function findingSeverityColor(severity: FindingSeverity): 'danger' | 'warning' | 'informative' | 'subtle' {
  if (severity === 'critical') return 'danger'
  if (severity === 'important' || severity === 'moderate') return 'warning'
  return severity === 'informational' || severity === 'other' ? 'subtle' : 'informative'
}
