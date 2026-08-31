import { useEffect, useState } from 'react'

import { Button, MessageBar, MessageBarBody, Spinner, Text } from '@fluentui/react-components'
import { ArrowLeftRegular, ArrowSyncRegular } from '@fluentui/react-icons'
import { Link, useLocation, useParams } from 'react-router'

import { scenariosApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { ScenarioRunSummary } from '@/types'
import { routerPathParamValue } from '@/utils/routeParams'

import { useScenarioRunStartedStyles } from './ScenarioRunStarted.styles'

type LoadStatus = 'loading' | 'success' | 'error'

/** Optional state forwarded by the launch form's `navigate()` call — shows a scenario name before the fetch resolves. */
interface ScenarioRunLocationState {
  scenarioName?: string
}

/**
 * Minimal acknowledgement shell shown right after launching a scenario run.
 *
 * Fetches the run once (no polling) to confirm it exists and show its
 * current status; it intentionally does not aggregate or poll progress —
 * that belongs to a full run-history view, out of scope here.
 */
export default function ScenarioRunStarted() {
  const { scenarioResultId: encodedId } = useParams<{ scenarioResultId: string }>()
  // Keying on the raw URL param forces a full remount (and state reset to the
  // initial "loading" values) if the route ever navigates from one run id
  // directly to another, without needing to reset state from inside an effect.
  return <ScenarioRunStartedContent key={encodedId} encodedId={encodedId} />
}

interface ScenarioRunStartedContentProps {
  encodedId: string | undefined
}

function ScenarioRunStartedContent({ encodedId }: ScenarioRunStartedContentProps) {
  const styles = useScenarioRunStartedStyles()
  const location = useLocation()
  const locationState = location.state as ScenarioRunLocationState | null
  const decodedId = routerPathParamValue(encodedId)

  const [run, setRun] = useState<ScenarioRunSummary | null>(null)
  const [status, setStatus] = useState<LoadStatus>('loading')
  const [error, setError] = useState<string | null>(null)
  const [refetchCount, setRefetchCount] = useState(0)

  useEffect(() => {
    let cancelled = false
    scenariosApi
      .getRun(decodedId)
      .then((data) => {
        if (cancelled) return
        setRun(data)
        setStatus('success')
        setError(null)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setRun(null)
        setStatus('error')
        setError(toApiError(err).detail)
      })
    return () => {
      cancelled = true
    }
  }, [decodedId, refetchCount])

  const handleRetry = (): void => {
    setStatus('loading')
    setError(null)
    setRefetchCount((count) => count + 1)
  }

  const displayScenarioName = run?.scenario_name ?? locationState?.scenarioName

  return (
    <div className={styles.root} data-testid="scenario-run-started">
      <Link to="/scanner" className={styles.backLink}>
        <ArrowLeftRegular /> Back to scanners
      </Link>

      <Text as="h1" size={600} weight="semibold">Scenario run started</Text>
      <Text size={200} className={styles.hint}>
        Run ID: <code>{decodedId}</code>
      </Text>

      {status === 'loading' && (
        <div className={styles.centeredState}>
          <Spinner label="Loading run status..." />
        </div>
      )}

      {status === 'error' && (
        <div className={styles.centeredState} data-testid="run-error">
          <MessageBar intent="error">
            <MessageBarBody>{error}</MessageBarBody>
          </MessageBar>
          <Button
            appearance="primary"
            icon={<ArrowSyncRegular />}
            onClick={handleRetry}
            data-testid="retry-btn"
          >
            Retry
          </Button>
        </div>
      )}

      {status === 'success' && run && (
        <div className={styles.section} data-testid="run-status">
          {displayScenarioName && (
            <Text size={300}>Scenario: <strong>{displayScenarioName}</strong></Text>
          )}
          <Text size={300}>
            Status: <strong data-testid="run-status-value">{run.status}</strong>
          </Text>
        </div>
      )}
    </div>
  )
}
