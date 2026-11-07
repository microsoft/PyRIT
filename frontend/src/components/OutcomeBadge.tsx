import type { ReactElement } from 'react'

import { Badge, Button, Popover, PopoverSurface, PopoverTrigger, Text, mergeClasses } from '@fluentui/react-components'
import type { BadgeProps } from '@fluentui/react-components'
import {
  ArrowSyncCircleRegular,
  CheckmarkCircleRegular,
  ClockRegular,
  DismissCircleRegular,
  ErrorCircleRegular,
  QuestionCircleRegular,
} from '@fluentui/react-icons'

import { formatAttackOutcome, formatAttackSuccess } from '@/components/AttackResults/attackAttemptFormatting'
import ScoreDetails from '@/components/Chat/ScoreDetails'
import type { AttackDisplayOutcome, ScoreSummary } from '@/types'

import { useOutcomeBadgeStyles } from './OutcomeBadge.styles'

const OUTCOME_ICONS: Record<AttackDisplayOutcome, ReactElement> = {
  success: <CheckmarkCircleRegular />,
  failure: <DismissCircleRegular />,
  error: <ErrorCircleRegular />,
  undetermined: <QuestionCircleRegular />,
  in_progress: <ArrowSyncCircleRegular />,
  preparing: <ClockRegular />,
}

const PENDING_OUTCOME_LABELS = {
  in_progress: 'In progress',
  preparing: 'Preparing',
} as const

/**
 * How a saved outcome is worded: `outcome` capitalizes it ("Success"), `success`
 * answers "Attack success?" ("True"/"False"). Omitted keeps the raw outcome word.
 */
type OutcomeLabelStyle = 'outcome' | 'success'

function outcomeLabel(outcome: AttackDisplayOutcome, labelStyle: OutcomeLabelStyle | undefined): string {
  if (outcome === 'in_progress' || outcome === 'preparing') return PENDING_OUTCOME_LABELS[outcome]
  if (labelStyle === 'success') return formatAttackSuccess(outcome)
  if (labelStyle === 'outcome') return formatAttackOutcome(outcome)
  return outcome
}

interface OutcomeBadgeProps {
  readonly outcome?: AttackDisplayOutcome | null
  readonly testId?: string
  readonly className?: string
  readonly appearance?: BadgeProps['appearance']
  readonly size?: BadgeProps['size']
  readonly label?: string
  readonly labelStyle?: OutcomeLabelStyle
  /** Opens the score behind the outcome from the badge. */
  readonly score?: ScoreSummary | null
  /** Shown in the score popover when no score was saved; also enables the popover without a score. */
  readonly scoreFallback?: string
  /** Accessible name of the badge button that opens the score. */
  readonly scoreButtonLabel?: string
}

interface OutcomeIconProps {
  readonly outcome?: AttackDisplayOutcome | null
}

export function OutcomeIcon({ outcome }: OutcomeIconProps) {
  const styles = useOutcomeBadgeStyles()
  const normalizedOutcome = outcome ?? 'undetermined'
  return (
    <span
      className={mergeClasses(styles.icon, styles[normalizedOutcome])}
      role="img"
      aria-label={normalizedOutcome.replace('_', ' ')}
    >
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
  labelStyle,
  score,
  scoreFallback,
  scoreButtonLabel = 'View score',
}: OutcomeBadgeProps) {
  const styles = useOutcomeBadgeStyles()
  const normalizedOutcome = outcome ?? 'undetermined'

  const badge = (
    <Badge
      appearance={appearance}
      icon={OUTCOME_ICONS[normalizedOutcome]}
      size={size}
      className={mergeClasses(styles[normalizedOutcome], styles[appearance], className)}
      data-testid={testId}
    >
      {label ?? outcomeLabel(normalizedOutcome, labelStyle)}
    </Badge>
  )
  if (!score && !scoreFallback) return badge

  return (
    <Popover withArrow>
      <PopoverTrigger disableButtonEnhancement>
        <Button appearance="transparent" className={styles.scoreTrigger} aria-label={scoreButtonLabel}>
          {badge}
        </Button>
      </PopoverTrigger>
      <PopoverSurface>
        {score ? <ScoreDetails score={score} /> : <Text>{scoreFallback}</Text>}
      </PopoverSurface>
    </Popover>
  )
}
