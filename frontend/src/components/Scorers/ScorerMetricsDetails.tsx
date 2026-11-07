import { Text } from '@fluentui/react-components'

import { DetailMetric } from '@/components/AttackResults/ComponentIdentityDetails'
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
  const renderMetric = (name: string) => <DetailMetric key={name}
    label={scorerMetricLabel(name)} value={formatScorerMetric(name, metrics[name], fallback)} />
  return (
    <div className={styles.metrics}>
      {[...full, ...Object.keys(metrics).filter((name: string) => !full.includes(name))].map(renderMetric)}
    </div>
  )
}
