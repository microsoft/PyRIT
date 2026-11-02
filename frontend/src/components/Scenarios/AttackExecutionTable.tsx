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
import { Link, useLocation, useNavigate } from 'react-router'

import {
  formatAttackOutcome,
  formatDuration,
  formatTimestamp,
  objectivePreview,
} from '@/components/AttackResults/attackAttemptFormatting'
import OutcomeBadge from '@/components/OutcomeBadge'
import type { HierarchyChildrenState, UseScenarioResultHierarchyResult } from '@/hooks/useScenarioResultHierarchy'
import type { ScenarioHierarchyNode, ScenarioProgressResult } from '@/types'
import { attackConversationRoutePath } from '@/utils/routeParams'

import { useAttackExecutionTableStyles } from './AttackExecutionTable.styles'
import { childSlots, hasChildReferences, resultRoleLabel } from './scenarioHierarchy'

interface AttackExecutionTableProps {
  readonly scenarioResultId: string
  readonly label: string
  readonly attempts: ScenarioProgressResult[]
  readonly totalAttempts: number
  readonly roots: ScenarioHierarchyNode[]
  readonly hierarchy: UseScenarioResultHierarchyResult
  readonly seedObjectives: Map<string, string>
  readonly onOpenDetails: (result: ScenarioProgressResult | ScenarioHierarchyNode, trigger: HTMLElement) => void
}

export default function AttackExecutionTable({
  scenarioResultId,
  label,
  attempts,
  totalAttempts,
  roots,
  hierarchy,
  seedObjectives,
  onOpenDetails,
}: AttackExecutionTableProps) {
  const styles = useAttackExecutionTableStyles()
  const savedRelationships = hierarchy.roots.status === 'ready'
  const results: readonly (ScenarioProgressResult | ScenarioHierarchyNode)[] = savedRelationships ? roots : attempts

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
        <TableHeader>
          <TableRow className={styles.headerRow}>
            <TableHeaderCell>Result / objective</TableHeaderCell>
            <TableHeaderCell>Result type</TableHeaderCell>
            <TableHeaderCell>Attack outcome</TableHeaderCell>
            <TableHeaderCell>Duration</TableHeaderCell>
            <TableHeaderCell>Retries / error</TableHeaderCell>
            <TableHeaderCell>Timestamp</TableHeaderCell>
            <TableHeaderCell>Actions</TableHeaderCell>
          </TableRow>
        </TableHeader>
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
                hierarchy={hierarchy}
                scenarioResultId={scenarioResultId}
                onOpenDetails={onOpenDetails}
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
  readonly result: ScenarioProgressResult | ScenarioHierarchyNode
  readonly objective: string
  readonly position: number | null
  readonly depth: number
  readonly ancestors: ReadonlySet<string>
  readonly hierarchy: UseScenarioResultHierarchyResult
  readonly scenarioResultId: string
  readonly onOpenDetails: AttackExecutionTableProps['onOpenDetails']
}

function ResultRows({
  nodeKey, result, objective, position, depth, ancestors, hierarchy, scenarioResultId, onOpenDetails,
}: ResultRowsProps) {
  const styles = useAttackExecutionTableStyles()
  const navigate = useNavigate()
  const location = useLocation()
  const node = 'objective' in result ? result : null
  const isExpanded = hierarchy.expanded.has(nodeKey)
  const expandable = node !== null && hasChildReferences(node)
  const preview = objectivePreview(objective || null, result.seed_group_id)
  const label = resultLabel(result, preview, position)
  const role = node?.result_role ?? 'unknown'
  const conversationPath = result.conversation_id
    ? attackConversationRoutePath(result.attack_result_id, result.conversation_id, scenarioResultId)
    : null

  const openResult = (trigger: HTMLElement): void => {
    if (conversationPath) {
      navigate(conversationPath, { state: location.state })
    } else if (expandable) {
      hierarchy.toggleNode(nodeKey, node)
    } else {
      onOpenDetails(result, trigger)
    }
  }

  return (
    <>
      <TableRow
        className={styles.resultRow}
        tabIndex={0}
        aria-label={`${conversationPath ? 'View conversation' : expandable ? 'View child results' : 'View details'} for ${label}`}
        onClick={(event) => {
          if (event.target instanceof Element && event.target.closest('a, button')) return
          openResult(event.currentTarget)
        }}
        onKeyDown={(event) => {
          if (event.target !== event.currentTarget) return
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault()
            openResult(event.currentTarget)
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
              {conversationPath ? (
                <Link className={styles.objectiveLink} to={conversationPath} state={location.state} title={objective}>
                  {preview}
                </Link>
              ) : (
                <Text title={objective}>{preview}</Text>
              )}
              <Text size={200} className={styles.hint}>{result.atomic_attack_name}</Text>
            </div>
          </div>
        </TableCell>
        <TableCell>
          <Badge appearance="outline" color={role === 'unknown' ? 'subtle' : 'brand'}>
            {resultRoleLabel(role)}
          </Badge>
        </TableCell>
        <TableCell>
          <OutcomeBadge appearance="tint" outcome={result.outcome} label={formatAttackOutcome(result.outcome)} />
        </TableCell>
        <TableCell className={styles.nowrap}>{formatDuration(result.execution_time_ms)}</TableCell>
        <TableCell>
          <Text>{result.total_retries}</Text>
          {result.outcome === 'error' && (
            <Text as="p" size={200} className={styles.hint}>
              {result.error_message ?? result.error_type ?? 'Error'}
            </Text>
          )}
        </TableCell>
        <TableCell className={styles.nowrap}>{formatTimestamp(result.timestamp)}</TableCell>
        <TableCell>
          <Button
            size="small"
            className={styles.touchTarget}
            aria-label={`Open details for ${label}`}
            onClick={(event) => {
              event.stopPropagation()
              onOpenDetails(result, event.currentTarget)
            }}
          >
            Details
          </Button>
        </TableCell>
      </TableRow>
      {node && expandable && isExpanded && (
        <ChildRows
          parentKey={nodeKey}
          parent={node}
          depth={depth + 1}
          ancestors={new Set([...ancestors, node.attack_result_id])}
          state={hierarchy.children.get(nodeKey)}
          hierarchy={hierarchy}
          scenarioResultId={scenarioResultId}
          onOpenDetails={onOpenDetails}
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
  readonly hierarchy: UseScenarioResultHierarchyResult
  readonly scenarioResultId: string
  readonly onOpenDetails: AttackExecutionTableProps['onOpenDetails']
}

function ChildRows({
  parentKey, parent, depth, ancestors, state, hierarchy, scenarioResultId, onOpenDetails,
}: ChildRowsProps) {
  const styles = useAttackExecutionTableStyles()
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
              hierarchy={hierarchy}
              scenarioResultId={scenarioResultId}
              onOpenDetails={onOpenDetails}
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
const COLUMN_COUNT = 7
const LABEL_OBJECTIVE_LENGTH = 60
const INDENT_STEP = '1.5rem'

function indent(depth: number): CSSProperties {
  return { paddingInlineStart: `calc(${depth} * ${INDENT_STEP})` }
}

function resultLabel(result: ScenarioProgressResult, objective: string, position: number | null): string {
  const text = objective.length > LABEL_OBJECTIVE_LENGTH
    ? `${objective.slice(0, LABEL_OBJECTIVE_LENGTH)}...`
    : objective
  return `${position === null ? '' : `#${position + 1} `}${result.atomic_attack_name}: ${text}`
}
