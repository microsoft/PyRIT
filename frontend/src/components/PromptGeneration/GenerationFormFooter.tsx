import {
  Button,
  MessageBar,
  MessageBarActions,
  MessageBarBody,
  MessageBarTitle,
  Text,
} from '@fluentui/react-components'
import { ArrowSyncRegular, SparkleRegular } from '@fluentui/react-icons'

import type { PreflightState } from '@/hooks/usePromptGenerationPreflight'
import type { PromptGenerationEstimate, PromptGeneratorDescriptor } from '@/types'

import GenerationEstimateCard from './GenerationEstimateCard'
import type { PreflightProblems } from './generationFormHelpers'
import { combineEstimates } from './promptGenerationPresentation'
import { usePromptGenerationStyles } from './PromptGeneration.styles'

interface GenerationFormFooterProps {
  readonly descriptor: PromptGeneratorDescriptor
  readonly clientErrors: readonly string[]
  readonly preflight: PreflightState
  readonly problems: PreflightProblems
  readonly datasetCount: number
  readonly submitting: boolean
  readonly submitError: string | null
  readonly metadataRefreshing?: boolean
  readonly onGenerate: () => void
}

/** Validation feedback, the estimate, and the generate action every generator form shares. */
export default function GenerationFormFooter({
  descriptor,
  clientErrors,
  preflight,
  problems,
  datasetCount,
  submitting,
  submitError,
  metadataRefreshing = false,
  onGenerate,
}: GenerationFormFooterProps) {
  const styles = usePromptGenerationStyles()
  const estimates = preflight.results
    .map((result) => result.estimate)
    .filter((estimate): estimate is PromptGenerationEstimate => estimate !== null)
  const estimate = clientErrors.length > 0 ? null : combineEstimates(estimates)
  const preflightReady = preflight.status === 'ready'
  const generateBlocked = submitting || metadataRefreshing || clientErrors.length > 0
    || preflight.status === 'checking' || preflight.status === 'idle'
    || (preflightReady && problems.blocksGenerate)
  const generateLabel = datasetCount > 1 ? `Generate ${datasetCount} datasets` : 'Generate'

  return (
    <>
      {clientErrors.length > 0 && (
        <MessageBar intent="info" data-testid="generation-form-errors">
          <MessageBarBody>
            <MessageBarTitle>To generate, complete the following</MessageBarTitle>
            <ul className={styles.list}>
              {[...new Set(clientErrors)].map((error) => <li key={error}><Text size={200}>{error}</Text></li>)}
            </ul>
          </MessageBarBody>
        </MessageBar>
      )}
      {clientErrors.length === 0 && preflightReady && problems.messages.length > 0 && (
        <MessageBar intent="warning" data-testid="generation-preflight-errors">
          <MessageBarBody>
            <MessageBarTitle>The server found problems</MessageBarTitle>
            <ul className={styles.list}>
              {problems.messages.map((message) => <li key={message}><Text size={200}>{message}</Text></li>)}
            </ul>
          </MessageBarBody>
        </MessageBar>
      )}
      {clientErrors.length === 0 && preflight.status === 'error' && (
        <MessageBar intent="warning" data-testid="generation-preflight-failed">
          <MessageBarBody>
            The request could not be checked: {preflight.error} You can still try to generate.
          </MessageBarBody>
          <MessageBarActions>
            <Button className={styles.touchTarget} icon={<ArrowSyncRegular />} onClick={preflight.retry}>
              Check again
            </Button>
          </MessageBarActions>
        </MessageBar>
      )}
      <GenerationEstimateCard
        estimate={estimate}
        descriptor={descriptor}
        updating={preflight.status === 'checking' && clientErrors.length === 0}
        datasetCount={datasetCount}
        emptyMessage={clientErrors.length > 0
          ? 'Complete the required fields to see an estimate.'
          : preflight.status === 'error' ? 'No estimate: the request could not be checked.'
            : preflight.status === 'ready' ? 'No estimate is available for this request.' : 'Calculating the estimate...'}
      />
      {metadataRefreshing && (
        <MessageBar intent="info" data-testid="generation-metadata-refreshing">
          <MessageBarBody>The backend restarted. Checking its generators before you can generate...</MessageBarBody>
        </MessageBar>
      )}
      {submitError && (
        <MessageBar intent="error">
          <MessageBarBody role="alert">{submitError}</MessageBarBody>
        </MessageBar>
      )}
      <div className={styles.actions}>
        <Button
          className={styles.touchTarget}
          appearance="primary"
          icon={<SparkleRegular />}
          disabled={generateBlocked}
          onClick={onGenerate}
          data-testid="generate-btn"
        >
          {submitting ? 'Submitting...' : generateLabel}
        </Button>

      </div>
    </>
  )
}
