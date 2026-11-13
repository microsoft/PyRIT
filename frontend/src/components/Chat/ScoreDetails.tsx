import { Badge, Text } from '@fluentui/react-components'

import type { ScoreSummary } from '@/types'
import { scoreSummaryValue } from '@/utils/scoreSummary'

import { useScoreDetailsStyles } from './ScoreDetails.styles'

interface ScoreDetailsProps {
  readonly score: ScoreSummary
  readonly testId?: string
}

/** One score's value, scorer, role, categories, and rationale, as shown in a score popover. */
export default function ScoreDetails({ score, testId }: ScoreDetailsProps) {
  const styles = useScoreDetailsStyles()
  const categories = score.categories?.filter(Boolean) ?? []

  return (
    <div className={styles.surface} data-testid={testId}>
      <Text weight="semibold">Score details</Text>
      <div className={styles.row}>
        <Text size={200} weight="semibold" className={styles.label}>Score</Text>
        <Badge appearance="tint" color="brand" size="small" className={styles.value}>{scoreSummaryValue(score)}</Badge>
      </div>
      <div className={styles.row}>
        <Text size={200} weight="semibold" className={styles.label}>Scorer</Text>
        <Text size={200} className={styles.value}>{score.scorer}</Text>
      </div>
      <div className={styles.row}>
        <Text size={200} weight="semibold" className={styles.label}>Result role</Text>
        <Text size={200} className={styles.value}>
          {score.isObjective ? 'Final score' : 'Supporting score'}
        </Text>
      </div>
      {categories.length > 0 && (
        <div className={styles.row}>
          <Text size={200} weight="semibold" className={styles.label}>Category</Text>
          <Text size={200} className={styles.value}>{categories.join(', ')}</Text>
        </div>
      )}
      {score.rationale && (
        <div className={styles.rationale}>
          <Text size={200} weight="semibold">Rationale</Text>
          <Text size={200} className={styles.rationaleText}>{score.rationale}</Text>
        </div>
      )}
    </div>
  )
}
