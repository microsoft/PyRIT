import { Spinner, Text } from '@fluentui/react-components'

import type { PromptGenerationEstimate, PromptGeneratorDescriptor } from '@/types'

import { formatRange, stageLabel } from './promptGenerationPresentation'
import { usePromptGenerationStyles } from './PromptGeneration.styles'

interface GenerationEstimateCardProps {
  readonly estimate: PromptGenerationEstimate | null
  readonly descriptor?: PromptGeneratorDescriptor
  /** True while a newer estimate is being calculated. */
  readonly updating?: boolean
  /** Shown instead of figures when there is no estimate yet. */
  readonly emptyMessage?: string
  readonly title?: string
  /** How many datasets the estimate covers, when more than one. */
  readonly datasetCount?: number
}

const APPROXIMATE_NOTICE = 'Approximate; excludes target-side usage such as web search, tool calls, and validation retries.'

/** Provider-call-free estimate of model calls and tokens. */
export default function GenerationEstimateCard({
  estimate,
  descriptor,
  updating = false,
  emptyMessage = 'Complete the required fields to see an estimate.',
  title = 'Estimate',
  datasetCount = 1,
}: GenerationEstimateCardProps) {
  const styles = usePromptGenerationStyles()
  return (
    <section className={styles.section} aria-label={title} data-testid="generation-estimate">
      <div className={styles.sectionHeading}>
        <Text as="h2" size={400} weight="semibold">{title}</Text>
        {updating && <Spinner size="extra-tiny" label="Updating estimate..." />}
      </div>
      {estimate === null ? (
        <Text className={styles.muted}>{emptyMessage}</Text>
      ) : (
        <>
          <dl className={styles.estimateList}>
            {datasetCount > 1 && (
              <div className={styles.estimateRow}>
                <dt>Datasets</dt>
                <dd>{datasetCount}</dd>
              </div>
            )}
            <div className={styles.estimateRow}>
              <dt>{estimate.exact_calls ? 'Model calls' : 'Model calls (range)'}</dt>
              <dd data-testid="estimate-calls">{formatRange(estimate.min_calls, estimate.max_calls)}</dd>
            </div>
            <div className={styles.estimateRow}>
              <dt>Tokens (input and output)</dt>
              <dd data-testid="estimate-tokens">{formatRange(estimate.low_tokens, estimate.high_tokens)}</dd>
            </div>
            {datasetCount === 1 && estimate.stages.map((stage) => (
              <div className={styles.estimateRow} key={`${stage.stage}:${stage.target_role}`}>
                <dt>{stageLabel(descriptor, stage.stage)} <span className={styles.muted}>({stage.target_role} target)</span></dt>
                <dd>{formatRange(stage.min_calls, stage.max_calls)} calls</dd>
              </div>
            ))}
          </dl>
          {estimate.notes.length > 0 && (
            <ul className={styles.list} aria-label="Estimate notes">
              {estimate.notes.map((note) => <li key={note}><Text size={200}>{note}</Text></li>)}
            </ul>
          )}
          <Text size={200} className={styles.muted}>{APPROXIMATE_NOTICE}</Text>
        </>
      )}
    </section>
  )
}
