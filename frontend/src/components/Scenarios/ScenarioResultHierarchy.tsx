import type { ReactNode } from 'react'

import { Button, MessageBar, MessageBarActions, MessageBarBody, Text } from '@fluentui/react-components'

import type { UseScenarioResultHierarchyResult } from '@/hooks/useScenarioResultHierarchy'
import type { ScenarioRunState } from '@/types'

import { useScenarioResultHierarchyStyles } from './ScenarioResultHierarchy.styles'

interface ScenarioResultHierarchyProps {
  readonly hierarchy: UseScenarioResultHierarchyResult
  readonly runStatus?: ScenarioRunState | null
  readonly children: ReactNode
}

/** Loading and continuation controls around the existing atomic attack-group tables. */
export default function ScenarioResultHierarchy({
  hierarchy,
  runStatus = null,
  children,
}: ScenarioResultHierarchyProps) {
  const styles = useScenarioResultHierarchyStyles()
  const { roots } = hierarchy
  const rootless = roots.diagnostics?.rootless_results_count ?? 0
  const rootlessSample = roots.diagnostics?.rootless_result_ids ?? []
  const currentStatus = runStatus ?? roots.runStatus
  const runActive = currentStatus !== null && ACTIVE_RUN_STATES.has(currentStatus)
  const statusChanged = runStatus !== null && roots.runStatus !== null && runStatus !== roots.runStatus

  return (
    <div className={styles.root}>
      {roots.status === 'loading' ? (
        <Text role="status" className={styles.hint}>
          Loading result relationships. Available executions are shown as flat rows while they load.
        </Text>
      ) : roots.status === 'error' ? (
        <MessageBar intent="error">
          <MessageBarBody>
            Could not load the result relationships: {roots.error}. Available executions are shown as flat rows.
          </MessageBarBody>
          <MessageBarActions>
            <Button className={styles.touchTarget} onClick={hierarchy.reloadRoots}>Retry</Button>
          </MessageBarActions>
        </MessageBar>
      ) : (
        <Text size={200} className={styles.hint}>
          Result rows are saved attacks. Orchestration rows are compound parents: expand them to see the attacks
          they ran in saved order, not chat messages or retries. Unresolved rows are references, not extra results.
          Click a result with a saved chat to open it; Details shows metadata. Group metrics come from the run
          summary, not a count of these rows.
        </Text>
      )}
      {roots.status === 'ready' && (runActive || statusChanged) && (
        <div className={styles.footer}>
          <Text size={200} className={styles.hint}>
            {runActive
              ? 'This run is still in progress, so these rows show the results saved when they loaded.'
              : "This run's status changed since these results loaded."}
          </Text>
          <Button size="small" className={styles.touchTarget} onClick={hierarchy.reloadRoots}>
            Reload hierarchy
          </Button>
        </div>
      )}
      {children}
      {roots.status === 'ready' && roots.groups.length === 0 && rootless === 0 && (
        <Text>No saved results are available for this run yet.</Text>
      )}
      {roots.truncated && (
        <Text size={200} className={styles.hint}>
          Some results are not in this preview, such as deeper levels or results it could not place. Expand a
          result to load its saved children.
        </Text>
      )}
      {rootless > 0 && (
        <Text size={200} className={styles.hint}>
          {rootless} saved {rootless === 1 ? 'result has' : 'results have'} no root in this run (for example, a
          cycle of references) and {rootless === 1 ? 'is' : 'are'} not shown in the tree.
          {rootlessSample.length > 0 &&
            ` ${roots.diagnostics?.rootless_results_truncated ? 'Some of their IDs' : 'Their IDs'}: ${rootlessSample.join(', ')}.`}
        </Text>
      )}
      {roots.staleCursor ? (
        <MessageBar intent="warning">
          <MessageBarBody>This run's saved results changed since they were loaded.</MessageBarBody>
          <MessageBarActions>
            <Button className={styles.touchTarget} onClick={hierarchy.reloadRoots}>Reload hierarchy</Button>
          </MessageBarActions>
        </MessageBar>
      ) : roots.status === 'ready' && roots.error ? (
        <MessageBar intent="error">
          <MessageBarBody>Could not load more results: {roots.error}</MessageBarBody>
          <MessageBarActions>
            <Button className={styles.touchTarget} onClick={hierarchy.loadMoreRoots}>Retry</Button>
          </MessageBarActions>
        </MessageBar>
      ) : null}
      {roots.nextCursor && !roots.staleCursor && !roots.error && (
        <div className={styles.footer}>
          <Button className={styles.touchTarget} disabled={roots.loadingMore} onClick={hierarchy.loadMoreRoots}>
            {roots.loadingMore ? 'Loading...' : 'Load more results'}
          </Button>
        </div>
      )}
    </div>
  )
}

const ACTIVE_RUN_STATES: ReadonlySet<ScenarioRunState> = new Set(['CREATED', 'QUEUED', 'IN_PROGRESS'])
