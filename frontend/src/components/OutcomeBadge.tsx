import { Badge, tokens } from '@fluentui/react-components'
import type { BadgeProps } from '@fluentui/react-components'
import {
  CheckmarkCircleRegular,
  DismissCircleRegular,
  ErrorCircleRegular,
  QuestionCircleRegular,
} from '@fluentui/react-icons'

import type { AttackOutcome } from '@/types'

const OUTCOME_ICONS: Record<AttackOutcome, React.ReactElement> = {
  success: <CheckmarkCircleRegular style={{ color: tokens.colorPaletteGreenForeground1 }} />,
  failure: <DismissCircleRegular style={{ color: tokens.colorPaletteRedForeground1 }} />,
  error: <ErrorCircleRegular style={{ color: tokens.colorPaletteRedForeground1 }} />,
  undetermined: <QuestionCircleRegular style={{ color: tokens.colorNeutralForeground3 }} />,
}

const OUTCOME_COLORS: Record<AttackOutcome, 'success' | 'danger' | 'informative' | 'warning'> = {
  success: 'success',
  failure: 'danger',
  error: 'warning',
  undetermined: 'informative',
}

interface OutcomeBadgeProps {
  outcome?: AttackOutcome | null
  testId?: string
  className?: string
  appearance?: BadgeProps['appearance']
  size?: BadgeProps['size']
}

export default function OutcomeBadge({
  outcome = 'undetermined',
  testId,
  className,
  appearance = 'filled',
  size,
}: OutcomeBadgeProps) {
  const normalizedOutcome = outcome ?? 'undetermined'

  return (
    <Badge
      appearance={appearance}
      color={OUTCOME_COLORS[normalizedOutcome]}
      icon={OUTCOME_ICONS[normalizedOutcome]}
      size={size}
      className={className}
      data-testid={testId}
    >
      {normalizedOutcome}
    </Badge>
  )
}
