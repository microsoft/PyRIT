import type { CSSProperties } from 'react'

import {
  Button,
  MessageBar,
  MessageBarActions,
  MessageBarBody,
  Table,
  TableBody,
  TableCell,
  TableRow,
  Text,
} from '@fluentui/react-components'
import { ChevronDownRegular, ChevronRightRegular } from '@fluentui/react-icons'
import { Link, useLocation } from 'react-router'

import {
  formatDuration,
  formatRetries,
  formatTimestamp,
  objectivePreview,
} from '@/components/AttackResults/attackAttemptFormatting'
import OutcomeBadge from '@/components/OutcomeBadge'
import type { HierarchyChildrenState, UseScenarioResultHierarchyResult } from '@/hooks/useScenarioResultHierarchy'
import type { ScenarioHierarchyNode, ScenarioProgressResult } from '@/types'
import { attackConversationRoutePath } from '@/utils/routeParams'
import { scoreSummaryFromProgressScore } from '@/utils/scoreSummary'

import { useAttackExecutionTableStyles } from './AttackExecutionTable.styles'
import { childSlots, hasChildReferences } from './scenarioHierarchy'

type AttackExecution = ScenarioProgressResult | ScenarioHierarchyNode

interface AttackExecutionTableProps {
  readonly scenarioResultId: string
  readonly label: string
  readonly attempts: ScenarioProgressResult[]
  readonly totalAttempts: number
  readonly roots: ScenarioHierarchyNode[]
  readonly hierarchy: UseScenarioResultHierarchyResult
  readonly seedObjectives: ReadonlyMap<string, string>
  readonly onOpenDetails: (result: AttackExecution, trigger: HTMLElement) => void
  /** Technique shorthand per atomic group, shown under each objective instead of the attack class. */
  readonly techniqueNames?: ReadonlyMap<string, string>
  readonly showTimestamp?: boolean
  readonly showConversationLink?: boolean
  readonly booleanOutcome?: boolean
}

/** How every row in one table renders, shared by top-level and child rows. */
interface RowOptions {
  readonly scenarioResultId: string
  readonly hierarchy: UseScenarioResultHierarchyResult
  readonly onOpenDetails: AttackExecutionTableProps['onOpenDetails']
  readonly techniqueNames?: ReadonlyMap<string, string>
  readonly showTimestamp: boolean
  readonly showConversationLink: boolean
  readonly booleanOutcome: boolean
  readonly columnCount: number
}

/** Attack executions as compact rows that open their details; compound parents expand to their saved children. */
export default function AttackExecutionTable({
  scenarioResultId,
  label,
  attempts,
  totalAttempts,
  roots,
  hierarchy,
  seedObjectives,
  onOpenDetails,
  techniqueNames,
  showTimestamp = true,
  showConversationLink = false,
  booleanOutcome = false,
}: AttackExecutionTableProps) {
  const styles = useAttackExecutionTableStyles()
  const options: RowOptions = {
    scenarioResultId, hierarchy, onOpenDetails, techniqueNames, showTimestamp, showConversationLink, booleanOutcome,
    columnCount: 4 + Number(showTimestamp) + Number(showConversationLink),
  }
  const savedRelationships = hierarchy.roots.status === 'ready'
  const results: readonly AttackExecution[] = savedRelationships ? roots : attempts

  if (results.length === 0) {
    return (
      <Text>
        {savedRelationships
          ? 'No top-level results are loaded for this group. Expand a parent or load more results.'
          : 'No execution details are available for this display group.'}
      </Text>
    )
  }

  return (
    <div className={styles.tableScroll}>
      {!savedRelationships && totalAttempts > attempts.length && (
        <Text size={200} className={styles.hint}>
          Showing the latest {attempts.length} of {totalAttempts} executions in this group.
        </Text>
      )}
      <Table size="small" className={styles.table} aria-label={label}>
        <TableBody>
          {results.map((result) => {
            const key = `${result.atomic_group_id}:${result.attack_result_id}`
            return (
              <ResultRows
                key={key}
                nodeKey={key}
                result={result}
                objective={'objective' in result
                  ? result.objective
                  : seedObjectives.get(result.seed_group_id) ?? ''}
                position={null}
                depth={0}
                ancestors={NO_ANCESTORS}
                options={options}
              />
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}

interface ResultRowsProps {
  readonly nodeKey: string
  readonly result: AttackExecution
  readonly objective: string
  readonly position: number | null
  readonly depth: number
  readonly ancestors: ReadonlySet<string>
  readonly options: RowOptions
}

function ResultRows({ nodeKey, result, objective, position, depth, ancestors, options }: ResultRowsProps) {
  const styles = useAttackExecutionTableStyles()
  const location = useLocation()
  const { hierarchy, onOpenDetails } = options
  const node = 'objective' in result ? result : null
  const isExpanded = hierarchy.expanded.has(nodeKey)
  const expandable = node !== null && hasChildReferences(node)
  const preview = objectivePreview(objective || null, result.seed_group_id)
  const label = resultLabel(result, preview, position)
  const conversationPath = result.conversation_id
    ? attackConversationRoutePath(result.attack_result_id, result.conversation_id, options.scenarioResultId)
    : null
  const error = result.outcome === 'error' ? result.error_message ?? result.error_type ?? 'Error' : null

  return (
    <>
      <TableRow
        className={styles.resultRow}
        tabIndex={0}
        aria-label={`View attack details for ${label}`}
        onClick={(event) => {
          if (event.target instanceof Element && event.target.closest('a, button')) return
          onOpenDetails(result, event.currentTarget)
        }}
        onKeyDown={(event) => {
          if (event.target !== event.currentTarget) return
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault()
            onOpenDetails(result, event.currentTarget)
          }
        }}
      >
        <TableCell>
          <div className={styles.resultCell} style={indent(depth)}>
            {expandable ? (
              <Button
                appearance="subtle"
                size="small"
                className={styles.touchTarget}
                icon={isExpanded ? <ChevronDownRegular /> : <ChevronRightRegular />}
                aria-expanded={isExpanded}
                aria-label={`${isExpanded ? 'Collapse' : 'Expand'} children of ${label}`}
                onClick={() => hierarchy.toggleNode(nodeKey, node)}
              />
            ) : (
              <span className={styles.expandSpacer} />
            )}
            {position !== null && <Text className={styles.position}>#{position + 1}</Text>}
            <div className={styles.identity}>
              <Text weight="semibold" title={objective}>{preview}</Text>
              <Text size={200} className={styles.hint}>
                {options.techniqueNames?.get(result.atomic_group_id) ?? result.atomic_attack_name}
              </Text>
            </div>
          </div>
        </TableCell>
        <TableCell aria-label="Attack outcome">
          <OutcomeBadge
            appearance="tint"
            outcome={result.outcome}
            labelStyle={options.booleanOutcome ? 'success' : 'outcome'}
            score={result.score ? scoreSummaryFromProgressScore(result.score) : null}
            scoreFallback={error ?? 'No score was saved for this result.'}
            scoreButtonLabel={`View score for ${label}`}
          />
        </TableCell>
        <TableCell aria-label="Duration" className={styles.nowrap}>{formatDuration(result.execution_time_ms)}</TableCell>
        <TableCell aria-label="Retries / error">
          <Text>{formatRetries(result.total_retries)}{error ? ' / error' : ''}</Text>
          {error && <Text as="p" size={200} className={styles.hint}>{error}</Text>}
        </TableCell>
        {options.showTimestamp && (
          <TableCell aria-label="Timestamp" className={styles.nowrap}>{formatTimestamp(result.timestamp)}</TableCell>
        )}
        {options.showConversationLink && (
          <TableCell className={styles.actions}>
            {conversationPath && <Link to={conversationPath} state={location.state}>Conversation</Link>}
          </TableCell>
        )}
      </TableRow>
      {node && expandable && isExpanded && (
        <ChildRows
          parentKey={nodeKey}
          parent={node}
          depth={depth + 1}
          ancestors={new Set([...ancestors, node.attack_result_id])}
          state={hierarchy.children.get(nodeKey)}
          options={options}
        />
      )}
    </>
  )
}

interface ChildRowsProps {
  readonly parentKey: string
  readonly parent: ScenarioHierarchyNode
  readonly depth: number
  readonly ancestors: ReadonlySet<string>
  readonly state: HierarchyChildrenState | undefined
  readonly options: RowOptions
}

function ChildRows({ parentKey, parent, depth, ancestors, state, options }: ChildRowsProps) {
  const styles = useAttackExecutionTableStyles()
  const { hierarchy, columnCount } = options
  const pages = state?.pages ?? []
  const slots = pages.flatMap(childSlots)
  return (
    <>
      {slots.map((slot) => {
        const cycle = slot.kind === 'child' && ancestors.has(slot.node.attack_result_id)
        if (slot.kind === 'child' && !cycle) {
          return (
            <ResultRows
              key={`${slot.position}:${slot.node.attack_result_id}`}
              nodeKey={`${parentKey}/${slot.position}:${slot.node.attack_result_id}`}
              result={slot.node}
              objective={slot.node.objective}
              position={slot.position}
              depth={depth}
              ancestors={ancestors}
              options={options}
            />
          )
        }
        const id = slot.kind === 'child' ? slot.node.attack_result_id : slot.attackResultId
        return (
          <TableRow key={`${slot.position}:${id}`}>
            <TableCell colSpan={columnCount}>
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
        <TableCell colSpan={columnCount}>
          <div className={styles.footer} style={indent(depth)}>
            {state?.loading && <Text role="status" className={styles.hint}>Loading children...</Text>}
            {!state?.loading && pages.length > 0 && (
              <Text size={200} className={styles.hint}>
                {state?.childrenCount != null
                  ? `Showing ${slots.length} of ${state.childrenCount} saved child positions`
                  : `Showing ${slots.length} saved child positions`}
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
                    onClick={() => pages.length > 0
                      ? hierarchy.loadMoreChildren(parentKey, parent)
                      : hierarchy.restartChildren(parentKey, parent)}
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

const NO_ANCESTORS: ReadonlySet<string> = new Set()
const LABEL_OBJECTIVE_LENGTH = 60
const INDENT_STEP = '1.5rem'

function indent(depth: number): CSSProperties {
  return { paddingInlineStart: `calc(${depth} * ${INDENT_STEP})` }
}

function resultLabel(result: ScenarioProgressResult | ScenarioHierarchyNode, objective: string, position: number | null): string {
  const text = objective.length > LABEL_OBJECTIVE_LENGTH
    ? `${objective.slice(0, LABEL_OBJECTIVE_LENGTH)}...`
    : objective
  return `${position === null ? '' : `#${position + 1} `}${result.atomic_attack_name}: ${text}`
}
