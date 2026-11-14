import type { ReactElement } from 'react'

import { Badge } from '@fluentui/react-components'
import type { BadgeProps } from '@fluentui/react-components'
import { CheckmarkCircleRegular, DismissCircleRegular, ErrorCircleRegular } from '@fluentui/react-icons'

import type { ScenarioRunState, ScenarioSuiteExecutionEntryState, ScenarioSuiteExecutionStatus } from '@/types'

type ScanStatus = ScenarioRunState | ScenarioSuiteExecutionStatus | ScenarioSuiteExecutionEntryState
type ScanStatusColor = 'informative' | 'brand' | 'success' | 'danger' | 'warning'

const SCAN_STATUS_COLORS: Record<ScanStatus, ScanStatusColor> = {
  PENDING: 'informative',
  SUBMITTING: 'informative',
  QUEUING: 'informative',
  CREATED: 'informative',
  QUEUED: 'informative',
  IN_PROGRESS: 'brand',
  COMPLETED: 'success',
  SUBMISSION_FAILED: 'danger',
  FAILED: 'danger',
  CANCELLED: 'warning',
}

const SCAN_STATUS_LABELS: Record<ScanStatus, string> = {
  PENDING: 'Pending',
  SUBMITTING: 'Submitting',
  QUEUING: 'Queuing',
  CREATED: 'Created',
  QUEUED: 'Queued',
  IN_PROGRESS: 'Executing',
  COMPLETED: 'Completed',
  SUBMISSION_FAILED: 'Submission failed',
  FAILED: 'Failed',
  CANCELLED: 'Cancelled',
}

function scanStatusIcon(status: ScanStatus): ReactElement | undefined {
  if (status === 'COMPLETED') return <CheckmarkCircleRegular />
  if (status === 'FAILED' || status === 'SUBMISSION_FAILED') return <ErrorCircleRegular />
  if (status === 'CANCELLED') return <DismissCircleRegular />
  return undefined
}

interface ScanStatusBadgeProps {
  readonly status: ScanStatus
  readonly appearance?: Extract<BadgeProps['appearance'], 'tint' | 'filled'>
  /** Mark terminal states with an icon as well as color. */
  readonly withIcon?: boolean
  /** Replaces the standard status label, such as a suite's wording for a running entry. */
  readonly label?: string
  readonly testId?: string
}

/** The lifecycle state of a scan, suite, or suite entry, with one color per state everywhere. */
export default function ScanStatusBadge({
  status,
  appearance = 'tint',
  withIcon = false,
  label,
  testId,
}: ScanStatusBadgeProps) {
  return (
    <Badge
      appearance={appearance}
      color={SCAN_STATUS_COLORS[status]}
      icon={withIcon ? scanStatusIcon(status) : undefined}
      data-testid={testId}
    >
      {label ?? SCAN_STATUS_LABELS[status]}
    </Badge>
  )
}
