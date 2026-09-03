import { Badge, MessageBar, MessageBarBody, Spinner, Text } from '@fluentui/react-components'

import type { ScenarioQueueEntry, ScenarioQueueSnapshot } from '@/types'

import { useScenarioQueueStyles } from './ScenarioQueue.styles'

interface ScenarioQueueProps {
  readonly snapshot: ScenarioQueueSnapshot | null
  readonly loading: boolean
  readonly stale: boolean
  readonly error: string | null
  readonly currentScenarioResultId?: string
}

export default function ScenarioQueue({
  snapshot,
  loading,
  stale,
  error,
  currentScenarioResultId,
}: ScenarioQueueProps) {
  const styles = useScenarioQueueStyles()
  const entries = snapshot
    ? [
        ...(snapshot.active ? [snapshot.active] : []),
        ...snapshot.queued,
      ]
    : []

  return (
    <section className={styles.root} aria-labelledby="scenario-queue-heading" data-testid="scenario-queue">
      <div className={styles.heading}>
        <Text as="h2" id="scenario-queue-heading" size={500} weight="semibold">Scenario queue</Text>
        <Text className={styles.hint}>One scenario executes at a time; waiting runs start FIFO.</Text>
      </div>
      {stale && error && (
        <MessageBar intent="warning">
          <MessageBarBody>Queue updates paused. Showing the last known order. {error}</MessageBarBody>
        </MessageBar>
      )}
      {loading && !snapshot ? (
        <Spinner size="tiny" label="Loading scenario queue..." />
      ) : error && !snapshot ? (
        <MessageBar intent="error"><MessageBarBody>{error}</MessageBarBody></MessageBar>
      ) : entries.length === 0 ? (
        <Text className={styles.empty}>No active or queued scenarios.</Text>
      ) : (
        <ol className={styles.list} aria-label="Scenario queue order">
          {entries.map((entry) => (
            <ScenarioQueueItem
              key={entry.scenario_result_id}
              entry={entry}
              current={entry.scenario_result_id === currentScenarioResultId}
            />
          ))}
        </ol>
      )}
    </section>
  )
}

interface ScenarioQueueItemProps {
  readonly entry: ScenarioQueueEntry
  readonly current: boolean
}

function ScenarioQueueItem({ entry, current }: ScenarioQueueItemProps) {
  const styles = useScenarioQueueStyles()
  const active = entry.state === 'IN_PROGRESS'
  const label = active ? 'Active' : `Position ${entry.position ?? '—'}`
  return (
    <li className={styles.entry}>
      <Badge appearance="tint" color={active ? 'brand' : 'informative'}>{label}</Badge>
      <a
        href={`/scenario-history/${encodeURIComponent(entry.scenario_result_id)}`}
        className={styles.link}
        aria-current={current ? 'page' : undefined}
      >
        <Text weight="semibold">{entry.scenario_registry_name || entry.scenario_name}</Text>
        <Text size={200} className={styles.runId}>{entry.scenario_result_id}</Text>
      </a>
      <Text size={200} className={styles.timestamp}>
        {active && entry.started_at ? `Started ${formatTimestamp(entry.started_at)}` : `Queued ${formatTimestamp(entry.enqueued_at)}`}
      </Text>
    </li>
  )
}

function formatTimestamp(timestamp: string): string {
  return new Date(timestamp).toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
  })
}
