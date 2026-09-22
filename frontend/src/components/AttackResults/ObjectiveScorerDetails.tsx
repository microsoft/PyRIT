import { Text } from '@fluentui/react-components'

import type { ScenarioObjectiveScorer } from '@/types'

import ComponentIdentityDetails, { DetailMetric } from './ComponentIdentityDetails'
import { useObjectiveScorerDetailsStyles } from './ObjectiveScorerDetails.styles'

const SCORER_METRICS_DOCUMENTATION_URL = 'https://microsoft.github.io/PyRIT/latest/code/scoring/scorer-metrics/'
const OMITTED_SCORER_PARAMETERS = new Set<string>(['scorer_type'])

interface ObjectiveScorerDetailsProps {
  readonly scorer: ScenarioObjectiveScorer
}

export default function ObjectiveScorerDetails({ scorer }: ObjectiveScorerDetailsProps) {
  const styles = useObjectiveScorerDetailsStyles()
  return (
    <article className={styles.card}>
      <div className={styles.section}>
        <ComponentIdentityDetails
          identity={scorer}
          omittedParameters={OMITTED_SCORER_PARAMETERS}
          collapseParameterValues
        />
      </div>
      <div className={styles.section}>
        <Text weight="semibold">Accuracy Metrics</Text>
        {scorer.metrics ? (
          <div className={styles.metrics}>
            <DetailMetric label="Accuracy" value={formatPercentage(scorer.metrics.accuracy)} />
            <DetailMetric
              label="Accuracy std error"
              value={formatOptionalDecimal(scorer.metrics.accuracy_standard_error)}
            />
            <DetailMetric label="F1 score" value={formatOptionalDecimal(scorer.metrics.f1_score)} />
            <DetailMetric label="Precision" value={formatOptionalDecimal(scorer.metrics.precision)} />
            <DetailMetric label="Recall" value={formatOptionalDecimal(scorer.metrics.recall)} />
            <DetailMetric
              label="Average score time"
              value={formatOptionalSeconds(scorer.metrics.average_score_time_seconds)}
            />
          </div>
        ) : (
          <Text className={styles.hint}>
            Official evaluation has not been run for this scorer configuration.
          </Text>
        )}
        <a
          className={styles.documentationLink}
          href={SCORER_METRICS_DOCUMENTATION_URL}
          target="_blank"
          rel="noopener noreferrer"
        >
          About scorer metrics
        </a>
      </div>
    </article>
  )
}

function formatPercentage(value: number): string {
  return `${(value * 100).toFixed(2)}%`
}

function formatOptionalDecimal(value: number | null | undefined): string {
  return value === null || value === undefined ? 'Unavailable' : value.toFixed(4)
}

function formatOptionalSeconds(value: number | null | undefined): string {
  return value === null || value === undefined ? 'Unavailable' : `${value.toFixed(2)}s`
}
