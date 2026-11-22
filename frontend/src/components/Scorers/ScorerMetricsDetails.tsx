import { Text } from '@fluentui/react-components'

import type { ScorerEvaluationMetrics } from '@/types'
import { formatScorerMetric, scorerMetricLabel } from '@/utils/scorerPresentation'

import { useScorerMetricsDetailsStyles } from './ScorerMetricsDetails.styles'

interface ScorerMetricsDetailsProps {
  metrics?: ScorerEvaluationMetrics | null
  scoreType?: string | null
  fallback?: string
}

export default function ScorerMetricsDetails({
  metrics, scoreType, fallback = 'n/a',
}: ScorerMetricsDetailsProps) {
  const styles = useScorerMetricsDetailsStyles()
  if (!metrics) return <Text>{fallback}</Text>
  const { num_responses: scored, num_input_responses: supplied } = metrics
  const coverage = typeof scored === 'number' && Number.isInteger(scored) && scored >= 0
    && typeof supplied === 'number' && Number.isInteger(supplied) && supplied > 0 && scored <= supplied
    ? `${scored} / ${supplied} scored`
    : undefined
  const full = scoreType === 'float_scale'
    ? ['mean_absolute_error', 'mae_standard_error', 'krippendorff_alpha_combined', 'average_score_time_seconds']
    : ['accuracy', 'accuracy_standard_error', 'f1_score', 'precision', 'recall', 'average_score_time_seconds']
  if (coverage !== undefined) full.splice(1, 0, 'coverage')
  const additional = Object.keys(metrics).filter(name => {
    if (name === 'num_responses' || name === 'num_input_responses') {
      return coverage === undefined && metrics[name] != null
    }
    return !full.includes(name)
  })
  const renderMetric = (name: string) => (
    <div key={name} className={styles.metric} role="group" aria-label={scorerMetricLabel(name)}>
      <Text size={200} className={styles.label}>{scorerMetricLabel(name)}</Text>
      <Text weight="semibold" className={styles.value}>
        {formatScorerMetric(name, name === 'coverage' && coverage !== undefined ? coverage : metrics[name], fallback)}
      </Text>
    </div>
  )
  return (
    <div className={styles.metrics}>
      {[...full, ...additional].map(renderMetric)}
    </div>
  )
}
