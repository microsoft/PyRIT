import {
  Button,
  Text,
  Badge,
  Popover,
  PopoverTrigger,
  PopoverSurface,
} from '@fluentui/react-components'
import type { AttackOutcome, ScoreView } from '../../types'
import { resolveOutcome } from '../../utils/attackOutcome'
import { getScoreColor } from '../../utils/scoreColor'
import { useAttackVerdictChipStyles } from './AttackVerdictChip.styles'

interface AttackVerdictChipProps {
  outcome?: AttackOutcome | null
  score?: ScoreView | null
}

export default function AttackVerdictChip({ outcome, score }: AttackVerdictChipProps) {
  const styles = useAttackVerdictChipStyles()

  // Only the score value is surfaced in the ribbon, so there's nothing to show without a score.
  if (!score) return null

  const resolvedOutcome = resolveOutcome(outcome)
  const categories = score.score_category?.filter(Boolean) ?? []
  const scoreColor = getScoreColor(score.score_type, score.score_value)
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
          aria-label={`Score ${score.score_value}`}
        >
          <span className={styles.chipLabel}>Score</span>
          <Badge appearance="filled" size="medium" style={scoreBadgeStyle}>
            {score.score_value}
          </Badge>
        </Button>
      </PopoverTrigger>
      <PopoverSurface>
        <div className={styles.surface} data-testid="attack-score-details">
          <Text weight="semibold" size={300}>Score</Text>
          <div className={styles.row}>
            <Text size={200} weight="semibold" className={styles.rowLabel}>Value</Text>
            <Badge appearance="filled" size="small" style={scoreBadgeStyle}>
              {score.score_value}
            </Badge>
          </div>
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
