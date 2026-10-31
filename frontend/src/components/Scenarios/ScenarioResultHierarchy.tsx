import type { CSSProperties } from 'react'

import {
  Badge,
  Button,
  MessageBar,
  MessageBarActions,
  MessageBarBody,
  Table,
  TableBody,
  TableCell,
  TableHeader,
  TableHeaderCell,
  TableRow,
  Text,
} from '@fluentui/react-components'
import { ChevronDownRegular, ChevronRightRegular } from '@fluentui/react-icons'

import OutcomeBadge from '@/components/OutcomeBadge'
import { formatAttackOutcome, formatDuration } from '@/components/AttackResults/attackAttemptFormatting'
import {
  useScenarioResultHierarchy,
  type HierarchyChildrenState,
  type UseScenarioResultHierarchyResult,
} from '@/hooks/useScenarioResultHierarchy'
import type { ScenarioHierarchyNode, ScenarioProgressCounts, ScenarioRunState } from '@/types'

import { childSlots, hasChildReferences, resultRoleLabel } from './scenarioHierarchy'
import { useScenarioResultHierarchyStyles } from './ScenarioResultHierarchy.styles'

interface ScenarioResultHierarchyProps {
  readonly scenarioResultId: string
  /** The run's current state, which can change (for example on resume) after the hierarchy loaded. */
  readonly runStatus?: ScenarioRunState | null
  readonly onOpenResult: (node: ScenarioHierarchyNode, trigger: HTMLElement) => void
}

/** Read-only browser for a run's saved result lineage: orchestration parents and their ordered children. */
export default function ScenarioResultHierarchy({
  scenarioResultId,
  runStatus = null,
  onOpenResult,
}: ScenarioResultHierarchyProps) {
  const styles = useScenarioResultHierarchyStyles()
  const hierarchy = useScenarioResultHierarchy(scenarioResultId)
  const { roots } = hierarchy

  // Plain status text, not a Spinner: a progressbar role here would be read as run progress.
  if (roots.status === 'loading') {
    return (
      <Text role="status" className={styles.hint}>
        Loading result hierarchy...
      </Text>
    )
  }

  if (roots.status === 'error') {
    return (
      <MessageBar intent="error">
        <MessageBarBody>Could not load the result hierarchy: {roots.error}</MessageBarBody>
        <MessageBarActions>
          <Button className={styles.touchTarget} onClick={hierarchy.reloadRoots}>Retry</Button>
        </MessageBarActions>
      </MessageBar>
    )
  }

  const rootless = roots.diagnostics?.rootless_results_count ?? 0
  const rootlessSample = roots.diagnostics?.rootless_result_ids ?? []
  const currentStatus = runStatus ?? roots.runStatus
  const runActive = currentStatus !== null && ACTIVE_RUN_STATES.has(currentStatus)
  const statusChanged = runStatus !== null && roots.runStatus !== null && runStatus !== roots.runStatus

  return (
    <div className={styles.root}>
      {roots.overallCounts && <RunTotals counts={roots.overallCounts} />}
      <Text size={200} className={styles.hint}>
        Totals cover the whole run as reported by the server; they are not counted from the rows shown here.
      </Text>
      {(runActive || statusChanged) && (
        <div className={styles.footer}>
          <Text size={200} className={styles.hint}>
            {runActive
              ? 'This run is still in progress, so this hierarchy shows the results saved when it loaded.'
              : "This run's status changed since this hierarchy loaded."}
          </Text>
          <Button size="small" className={styles.touchTarget} onClick={hierarchy.reloadRoots}>
            Reload hierarchy
          </Button>
        </div>
      )}

      {roots.groups.length === 0 ? (
        rootless === 0 && <Text>No saved results are available for this run yet.</Text>
      ) : (
        <>
          <Text size={200} className={styles.hint}>
            Each row is one saved attack result. An orchestration row summarizes a compound attack; expand it to
            see its saved component results, numbered in order. These are separate attack results, not chat
            messages or retries.
          </Text>
          {roots.groups.map((group) => (
            <section key={group.atomic_group_id} className={styles.group} aria-label={`${group.display_group} results`}>
              <Text weight="semibold">{group.display_group}</Text>
              <Text size={200} className={styles.hint}>
                {group.atomic_attack_name} · {formatCounts(group.counts)}
              </Text>
              <div className={styles.tableScroll}>
                <Table size="small" className={styles.table} aria-label={`${group.display_group} saved results`}>
                  <TableHeader>
                    <TableRow>
                      <TableHeaderCell>Result / objective</TableHeaderCell>
                      <TableHeaderCell>Result type</TableHeaderCell>
                      <TableHeaderCell>Attack outcome</TableHeaderCell>
                      <TableHeaderCell>Duration</TableHeaderCell>
                      <TableHeaderCell>Retries</TableHeaderCell>
                      <TableHeaderCell>Actions</TableHeaderCell>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {group.roots.map((node, i) => {
                      const key = `${group.atomic_group_id}:${i}:${node.attack_result_id}`
                      return (
                        <HierarchyNodeRows
                          key={key}
                          nodeKey={key}
                          node={node}
                          position={null}
                          depth={0}
                          ancestors={NO_ANCESTORS}
                          hierarchy={hierarchy}
                          onOpenResult={onOpenResult}
                        />
                      )
                    })}
                  </TableBody>
                </Table>
              </div>
            </section>
          ))}
        </>
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
      ) : roots.error ? (
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

interface HierarchyNodeRowsProps {
  readonly nodeKey: string
  readonly node: ScenarioHierarchyNode
  /** Absolute position under the parent, or null for a root. */
  readonly position: number | null
  readonly depth: number
  /** Results on the path above this one; a child that is one of them closes a cycle. */
  readonly ancestors: ReadonlySet<string>
  readonly hierarchy: UseScenarioResultHierarchyResult
  readonly onOpenResult: (node: ScenarioHierarchyNode, trigger: HTMLElement) => void
}

/** A result's row, followed by its child rows when expanded. Only the first column is indented. */
function HierarchyNodeRows({
  nodeKey,
  node,
  position,
  depth,
  ancestors,
  hierarchy,
  onOpenResult,
}: HierarchyNodeRowsProps) {
  const styles = useScenarioResultHierarchyStyles()
  const isExpanded = hierarchy.expanded.has(nodeKey)
  const expandable = hasChildReferences(node)
  const label = nodeLabel(node, position)

  return (
    <>
      <TableRow>
        <TableCell>
          <div className={styles.resultCell} style={indent(depth)}>
            {expandable ? (
              <Button
                appearance="subtle"
                size="small"
                className={styles.expandButton}
                icon={isExpanded ? <ChevronDownRegular /> : <ChevronRightRegular />}
                aria-expanded={isExpanded}
                aria-label={`${isExpanded ? 'Collapse' : 'Expand'} children of ${label}`}
                onClick={() => hierarchy.toggleNode(nodeKey, node)}
              />
            ) : (
              <span className={styles.expandSpacer} />
            )}
            {position !== null && <Text className={styles.position}>#{position + 1}</Text>}
            <Text className={styles.objective}>{node.objective}</Text>
          </div>
        </TableCell>
        <TableCell>
          <Badge appearance="outline" color={node.result_role === 'unknown' ? 'subtle' : 'brand'}>
            {resultRoleLabel(node.result_role)}
          </Badge>
        </TableCell>
        <TableCell>
          <div className={styles.outcomeCell}>
            <OutcomeBadge appearance="tint" outcome={node.outcome} label={formatAttackOutcome(node.outcome)} />
            {node.outcome === 'error' && (node.error_message || node.error_type) && (
              <Text size={200} className={styles.hint}>
                {node.error_message ?? node.error_type}
              </Text>
            )}
          </div>
        </TableCell>
        <TableCell className={styles.nowrap}>{formatDuration(node.execution_time_ms)}</TableCell>
        <TableCell className={styles.nowrap}>{node.total_retries}</TableCell>
        <TableCell>
          <Button
            size="small"
            className={styles.touchTarget}
            aria-label={`Open details for ${label}`}
            onClick={(event) => onOpenResult(node, event.currentTarget)}
          >
            Details
          </Button>
        </TableCell>
      </TableRow>
      {expandable && isExpanded && (
        <ChildRows
          parentKey={nodeKey}
          parent={node}
          depth={depth + 1}
          ancestors={new Set([...ancestors, node.attack_result_id])}
          state={hierarchy.children.get(nodeKey)}
          hierarchy={hierarchy}
          onOpenResult={onOpenResult}
        />
      )}
    </>
  )
}

interface ChildRowsProps {
  readonly parentKey: string
  readonly parent: ScenarioHierarchyNode
  readonly depth: number
  /** The parent and everything above it. */
  readonly ancestors: ReadonlySet<string>
  readonly state: HierarchyChildrenState | undefined
  readonly hierarchy: UseScenarioResultHierarchyResult
  readonly onOpenResult: (node: ScenarioHierarchyNode, trigger: HTMLElement) => void
}

function ChildRows({ parentKey, parent, depth, ancestors, state, hierarchy, onOpenResult }: ChildRowsProps) {
  const styles = useScenarioResultHierarchyStyles()
  const pages = state?.pages ?? []
  const slots = pages.flatMap(childSlots)
  const loaded = slots.length
  const total = state?.childrenCount ?? null

  return (
    <>
      {slots.map((slot) => {
        // A fetched page can't know the path to it, so a reference back up the path is caught here.
        const cycle = slot.kind === 'child' && ancestors.has(slot.node.attack_result_id)
        if (slot.kind === 'child' && !cycle) {
          return (
            <HierarchyNodeRows
              key={`${slot.position}:${slot.node.attack_result_id}`}
              nodeKey={`${parentKey}/${slot.position}:${slot.node.attack_result_id}`}
              node={slot.node}
              position={slot.position}
              depth={depth}
              ancestors={ancestors}
              hierarchy={hierarchy}
              onOpenResult={onOpenResult}
            />
          )
        }
        const id = slot.kind === 'child' ? slot.node.attack_result_id : slot.attackResultId
        return (
          <TableRow key={`${slot.position}:${id}`}>
            <TableCell colSpan={COLUMN_COUNT}>
              <div className={styles.resultCell} style={indent(depth)}>
                <span className={styles.expandSpacer} />
                <Text className={styles.position}>#{slot.position + 1}</Text>
                <Text className={styles.unresolved}>
                  Unresolved reference: {id}
                  {cycle && ' (it points back to a result above it)'}
                </Text>
              </div>
            </TableCell>
          </TableRow>
        )
      })}
      <TableRow>
        <TableCell colSpan={COLUMN_COUNT}>
          <div className={styles.footer} style={indent(depth)}>
            {state?.loading && (
              <Text role="status" size={200} className={styles.hint}>
                Loading children...
              </Text>
            )}
            {!state?.loading && pages.length > 0 && (
              <Text size={200} className={styles.hint}>
                {total !== null ? `Showing ${loaded} of ${total} saved children` : `Showing ${loaded} saved children`}
              </Text>
            )}
            {state?.staleCursor ? (
              <MessageBar intent="warning">
                <MessageBarBody>This result's saved children changed since they were loaded.</MessageBarBody>
                <MessageBarActions>
                  <Button className={styles.touchTarget} onClick={() => hierarchy.restartChildren(parentKey, parent)}>
                    Reload children
                  </Button>
                </MessageBarActions>
              </MessageBar>
            ) : state?.error ? (
              <MessageBar intent="error">
                <MessageBarBody>Could not load children: {state.error}</MessageBarBody>
                <MessageBarActions>
                  <Button
                    className={styles.touchTarget}
                    onClick={() =>
                      pages.length > 0
                        ? hierarchy.loadMoreChildren(parentKey, parent)
                        : hierarchy.restartChildren(parentKey, parent)
                    }
                  >
                    Retry
                  </Button>
                </MessageBarActions>
              </MessageBar>
            ) : null}
            {state?.nextCursor && !state.loading && !state.staleCursor && !state.error && (
              <Button className={styles.touchTarget} onClick={() => hierarchy.loadMoreChildren(parentKey, parent)}>
                Load more children
              </Button>
            )}
          </div>
        </TableCell>
      </TableRow>
    </>
  )
}

function RunTotals({ counts }: { readonly counts: ScenarioProgressCounts }) {
  const styles = useScenarioResultHierarchyStyles()
  return (
    <div className={styles.totals} aria-label="Run totals">
      <Text>Completed: {counts.planned !== null ? `${counts.completed} of ${counts.planned}` : counts.completed}</Text>
      <Text>Succeeded: {counts.succeeded}</Text>
      <Text>Errors: {counts.errors}</Text>
      <Text>Retries: {counts.retries}</Text>
    </div>
  )
}

const ACTIVE_RUN_STATES: ReadonlySet<ScenarioRunState> = new Set(['CREATED', 'QUEUED', 'IN_PROGRESS'])
const NO_ANCESTORS: ReadonlySet<string> = new Set()
const COLUMN_COUNT = 6
const LABEL_OBJECTIVE_LENGTH = 60
const INDENT_STEP = '1.5rem'

/** Nesting depth, shown only in the first column so the other columns stay aligned. */
function indent(depth: number): CSSProperties {
  return { paddingInlineStart: `calc(${depth} * ${INDENT_STEP})` }
}

/** Accessible name for a result; sibling results usually share an attack name, so add position and objective. */
function nodeLabel(node: ScenarioHierarchyNode, position: number | null): string {
  const objective =
    node.objective.length > LABEL_OBJECTIVE_LENGTH
      ? `${node.objective.slice(0, LABEL_OBJECTIVE_LENGTH)}...`
      : node.objective
  const prefix = position === null ? '' : `#${position + 1} `
  return `${prefix}${node.atomic_attack_name}: ${objective}`
}

function formatCounts(counts: ScenarioProgressCounts): string {
  const completed = counts.planned !== null ? `${counts.completed} of ${counts.planned}` : `${counts.completed}`
  return `${completed} completed, ${counts.succeeded} succeeded, ${counts.errors} errors, ${counts.retries} retries`
}
