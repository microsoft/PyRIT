import {
  Button,
  Text,
  Badge,
  Popover,
  PopoverTrigger,
  PopoverSurface,
} from '@fluentui/react-components'
import type { AttackOutcome, ScoreView } from '../../types'
import { OUTCOME_COLORS, OUTCOME_ICONS, resolveOutcome } from '../../utils/attackOutcome'
import { getScoreColor } from '../../utils/scoreColor'
import { useAttackVerdictChipStyles } from './AttackVerdictChip.styles'

interface AttackVerdictChipProps {
  outcome?: AttackOutcome | null
  score?: ScoreView | null
}

export default function AttackVerdictChip({ outcome, score }: AttackVerdictChipProps) {
  const styles = useAttackVerdictChipStyles()

  // The verdict is the attack's outcome plus its score. If both are missing, there's nothing to show
  // (e.g. a manual GUI attack, which can't be scored yet), so the chip is omitted entirely.
  if (!outcome && !score) return null
  const resolvedOutcome = resolveOutcome(outcome)
  const outcomeBadge = (
    <Badge
      appearance="filled"
      color={OUTCOME_COLORS[resolvedOutcome]}
      icon={OUTCOME_ICONS[resolvedOutcome]}
      data-testid="attack-outcome-badge"
    >
      {resolvedOutcome}
    </Badge>
  )

  // Outcome-only verdict: strategies that produce an outcome but no score (e.g. sequential,
  // barge-in) still get the outcome badge, matching the history table, with no score popover.
  if (!score) {
    return (
      <div className={styles.chip} data-testid="attack-verdict-chip">
        {outcomeBadge}
      </div>
    )
  }

  const categories = score.score_category?.filter(Boolean) ?? []
  // A FloatScaleThresholdScorer keeps the raw scale score behind the true/false
  // score; surface the float value and let it determine the badge tone.
  const scaleScore = score.scale_score ?? null
  const scoreColor = getScoreColor(resolvedOutcome, score.score_type, score.score_value, scaleScore)
  const scoreBadgeStyle = {
    backgroundColor: scoreColor.background,
    borderColor: scoreColor.background,
    color: scoreColor.foreground,
  }

  return (
    <Popover withArrow>
      <PopoverTrigger disableButtonEnhancement>
        <Button
          appearance="subtle"
          className={styles.chip}
          data-testid="attack-score-chip"
          aria-label={`Verdict ${resolvedOutcome}, score ${score.score_value}`}
        >
          <Badge appearance="filled" size="medium" style={scoreBadgeStyle}>
            {resolvedOutcome}
          </Badge>
        </Button>
      </PopoverTrigger>
      <PopoverSurface>
        <div className={styles.surface} data-testid="attack-score-details">
          <Text weight="semibold" size={300}>Verdict</Text>
          <div className={styles.row}>
            <Text size={200} weight="semibold" className={styles.rowLabel}>Value</Text>
            <Badge appearance="filled" size="small" style={scoreBadgeStyle}>
              {score.score_value}
            </Badge>
          </div>
          {scaleScore !== null && (
            <div className={styles.row}>
              <Text size={200} weight="semibold" className={styles.rowLabel}>Scale score</Text>
              <Text size={200}>{scaleScore.toFixed(2)}</Text>
            </div>
          )}
          <div className={styles.row}>
            <Text size={200} weight="semibold" className={styles.rowLabel}>Type</Text>
            <Text size={200}>{score.score_type}</Text>
          </div>
          <div className={styles.row}>
            <Text size={200} weight="semibold" className={styles.rowLabel}>Scorer</Text>
            <Text size={200}>{score.scorer_type}</Text>
          </div>
          {categories.length > 0 && (
            <div className={styles.row}>
              <Text size={200} weight="semibold" className={styles.rowLabel}>Category</Text>
              <Text size={200}>{categories.join(', ')}</Text>
            </div>
          )}
          <div className={styles.row}>
            <Text size={200} weight="semibold" className={styles.rowLabel}>Outcome</Text>
            <Text size={200} className={styles.chipLabel}>{resolvedOutcome}</Text>
          </div>
          {score.score_rationale && (
            <div className={styles.rationaleBlock}>
              <Text size={200} weight="semibold">Rationale</Text>
              <Text size={200} className={styles.rationaleText}>{score.score_rationale}</Text>
            </div>
          )}
        </div>
      </PopoverSurface>
    </Popover>
  )
}
