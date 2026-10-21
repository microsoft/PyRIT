import { Text } from '@fluentui/react-components'

import type { ScenarioObjectiveScorer } from '@/types'

import ScorerMetricsDetails from '@/components/Scorers/ScorerMetricsDetails'
import ComponentIdentityDetails from './ComponentIdentityDetails'
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
          <ScorerMetricsDetails metrics={{ ...scorer.metrics }} fallback="Unavailable" />
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
