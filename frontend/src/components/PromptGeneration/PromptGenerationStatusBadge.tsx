import type { ReactElement } from 'react'

import { Badge } from '@fluentui/react-components'
import {
  CheckmarkCircleRegular,
  DismissCircleRegular,
  ErrorCircleRegular,
  PauseCircleRegular,
} from '@fluentui/react-icons'

import type { PromptGenerationStatus } from '@/types'

import { GENERATION_STATUS_COLORS, generationStatusLabel } from './promptGenerationPresentation'

interface PromptGenerationStatusBadgeProps {
  readonly status: PromptGenerationStatus
  readonly appearance?: 'filled' | 'tint'
  readonly testId?: string
}

function statusIcon(status: PromptGenerationStatus): ReactElement | undefined {
  switch (status) {
    case 'completed':
      return <CheckmarkCircleRegular />
    case 'failed':
    case 'interrupted':
      return <ErrorCircleRegular />
    case 'cancelled':
      return <DismissCircleRegular />
    case 'awaiting_review':
      return <PauseCircleRegular />
    default:
      return undefined
  }
}

export default function PromptGenerationStatusBadge({
  status,
  appearance = 'filled',
  testId,
}: PromptGenerationStatusBadgeProps) {
  return (
    <Badge
      appearance={appearance}
      color={GENERATION_STATUS_COLORS[status] ?? 'informative'}
      icon={statusIcon(status)}
      data-testid={testId}
    >
      {generationStatusLabel(status)}
    </Badge>
  )
}
