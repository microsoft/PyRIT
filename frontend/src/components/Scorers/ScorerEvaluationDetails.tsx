import { useId } from 'react'

import { Button, Link, Popover, PopoverSurface, PopoverTrigger, Text } from '@fluentui/react-components'

import IdentityDisclosure from '@/components/Registry/IdentityDisclosure'
import type { ComponentIdentifier, ScorerEvaluationMetrics } from '@/types'

import ScorerMetricsDetails from './ScorerMetricsDetails'
import { useScorerEvaluationDetailsStyles } from './ScorerEvaluationDetails.styles'

const SCORER_METRICS_DOCUMENTATION_URL = 'https://microsoft.github.io/PyRIT/latest/code/scoring/scorer-metrics/'

interface ScorerEvaluationDetailsProps {
  readonly name: string
  readonly metrics?: ScorerEvaluationMetrics | null
  readonly scoreType?: string | null
  readonly evaluationIdentity?: ComponentIdentifier | null
  readonly recorded?: boolean
}

export default function ScorerEvaluationDetails({
  name, metrics, scoreType, evaluationIdentity, recorded = false,
}: ScorerEvaluationDetailsProps) {
  const styles = useScorerEvaluationDetailsStyles()
  const headingId = useId()
  const hasMetrics = metrics && Object.values(metrics).some((value: ScorerEvaluationMetrics[string]) => value != null)
  return (
    <Popover withArrow trapFocus>
      <PopoverTrigger disableButtonEnhancement>
        <Button appearance="transparent" size="small" className={styles.trigger}
          aria-label={`Evaluation metrics for ${name}`}>
          Evaluation metrics
        </Button>
      </PopoverTrigger>
      <PopoverSurface role="dialog" aria-labelledby={headingId} className={styles.surface}>
        <Text as="h3" id={headingId} size={400} weight="semibold" className={styles.heading}>
          Scorer evaluation metrics
        </Text>
        <Text className={styles.description}>
          {recorded ? 'Quality of the recorded scorer configuration' : 'Quality of this scorer configuration'},
          not an individual attack score or the scan success rate.
        </Text>
        {hasMetrics ? (
          <ScorerMetricsDetails metrics={metrics} scoreType={scoreType} fallback="Unavailable" />
        ) : (
          <Text>No official evaluation metrics are available for this scorer configuration.</Text>
        )}
        {evaluationIdentity && (
          <IdentityDisclosure name="Behavioral evaluation identity" identity={evaluationIdentity} />
        )}
        <Link href={SCORER_METRICS_DOCUMENTATION_URL} target="_blank" rel="noopener noreferrer">
          About scorer metrics
        </Link>
      </PopoverSurface>
    </Popover>
  )
}
