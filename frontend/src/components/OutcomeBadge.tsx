import type { ReactElement } from 'react'

import { Badge, mergeClasses } from '@fluentui/react-components'
import type { BadgeProps } from '@fluentui/react-components'
import {
  CheckmarkCircleRegular,
  DismissCircleRegular,
  ErrorCircleRegular,
  QuestionCircleRegular,
} from '@fluentui/react-icons'

import type { AttackOutcome } from '@/types'

import { useOutcomeBadgeStyles } from './OutcomeBadge.styles'

const OUTCOME_ICONS: Record<AttackOutcome, ReactElement> = {
  success: <CheckmarkCircleRegular />,
  failure: <DismissCircleRegular />,
  error: <ErrorCircleRegular />,
  undetermined: <QuestionCircleRegular />,
}

interface OutcomeBadgeProps {
  readonly outcome?: AttackOutcome | null
  readonly testId?: string
  readonly className?: string
  readonly appearance?: BadgeProps['appearance']
  readonly size?: BadgeProps['size']
  readonly label?: string
}

interface OutcomeIconProps {
  readonly outcome?: AttackOutcome | null
}

export function OutcomeIcon({ outcome }: OutcomeIconProps) {
  const styles = useOutcomeBadgeStyles()
  const normalizedOutcome = outcome ?? 'undetermined'
  return (
    <span className={mergeClasses(styles.icon, styles[normalizedOutcome])} role="img" aria-label={normalizedOutcome}>
      {OUTCOME_ICONS[normalizedOutcome]}
    </span>
  )
}

export default function OutcomeBadge({
  outcome = 'undetermined',
  testId,
  className,
  appearance = 'filled',
  size,
  label,
}: OutcomeBadgeProps) {
  const styles = useOutcomeBadgeStyles()
  const normalizedOutcome = outcome ?? 'undetermined'

  return (
    <Badge
      appearance={appearance}
      icon={OUTCOME_ICONS[normalizedOutcome]}
      size={size}
      className={mergeClasses(styles[normalizedOutcome], styles[appearance], className)}
      data-testid={testId}
    >
      {label ?? normalizedOutcome}
    </Badge>
  )
}
