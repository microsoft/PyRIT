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
  const full = scoreType === 'float_scale'
    ? ['mean_absolute_error', 'mae_standard_error', 'krippendorff_alpha_combined', 'average_score_time_seconds']
    : ['accuracy', 'accuracy_standard_error', 'f1_score', 'precision', 'recall', 'average_score_time_seconds']
  const renderMetric = (name: string) => (
    <div key={name} className={styles.metric} role="group" aria-label={scorerMetricLabel(name)}>
      <Text size={200} className={styles.label}>{scorerMetricLabel(name)}</Text>
      <Text weight="semibold" className={styles.value}>{formatScorerMetric(name, metrics[name], fallback)}</Text>
    </div>
  )
  return (
    <div className={styles.metrics}>
      {[...full, ...Object.keys(metrics).filter((name: string) => !full.includes(name))].map(renderMetric)}
    </div>
  )
}
