import { useEffect, useId, useRef, useState } from 'react'
import { Link } from 'react-router'

import {
  Button,
  Field,
  MessageBar,
  MessageBarBody,
  Select,
  Spinner,
  Table,
  TableBody,
  TableCell,
  TableHeader,
  TableHeaderCell,
  TableRow,
  Text,
} from '@fluentui/react-components'

import { scenariosApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type {
  PairedOutcomeDetail,
  PairedOutcomeDetailSide,
  PairedOutcomeStatistics,
  PairedOutcomeTransition,
  ScenarioRunPlan,
  ScenarioRunState,
} from '@/types'
import { attackConversationRoutePath } from '@/utils/routeParams'

import { usePairedOutcomesStyles } from './PairedOutcomes.styles'

interface PairedOutcomesProps {
  readonly scenarioResultId: string
  readonly status: ScenarioRunState
  readonly plan: ScenarioRunPlan | null
}

interface ComparisonProps {
  readonly scenarioResultId: string
  readonly baselineId: string
  readonly attackId: string
}

const DETAIL_PAGE_SIZE = 25

const TRANSITIONS: ReadonlyArray<{ label: string; id: PairedOutcomeTransition }> = [
  { label: 'Failure → Success', id: 'failure_to_success' },
  { label: 'Success → Failure', id: 'success_to_failure' },
  { label: 'Failure → Failure', id: 'failure_to_failure' },
  { label: 'Success → Success', id: 'success_to_success' },
]

/** Explicitly select two planned groups; the backend owns all matching and counting. */
export default function PairedOutcomes({ scenarioResultId, status, plan }: PairedOutcomesProps) {
  const styles = usePairedOutcomesStyles()
  const headingId = useId()
  const [baselineId, setBaselineId] = useState('')
  const [attackId, setAttackId] = useState('')
  const [comparison, setComparison] = useState<{
    baselineId: string
    attackId: string
    version: number
  } | null>(null)
  const baselines = plan?.atomic_groups.filter((group) => group.kind === 'baseline') ?? []
  const attacks = plan?.atomic_groups.filter((group) => group.kind === 'attack') ?? []
  const unavailable = status !== 'COMPLETED'
    ? 'Complete the run before comparing baseline and attack outcomes.'
    : !plan
    ? 'Paired outcomes require a saved execution plan.'
    : baselines.length === 0 || attacks.length === 0
    ? 'This run needs an explicitly identified baseline and an attack group to compare outcomes.'
    : null
  const validSelection = baselines.some((group) => group.id === baselineId)
    && attacks.some((group) => group.id === attackId)

  return (
    <section className={styles.root} aria-labelledby={headingId}>
      <Text as="h2" id={headingId} size={500} weight="semibold">Paired outcomes</Text>
      {unavailable ? <Text>{unavailable}</Text> : (
        <>
          <Text>Compare the same logical objectives before and after an attack.</Text>
          <div className={styles.controls}>
            <Field label="Baseline group" className={styles.selector}>
              <Select
                value={baselineId}
                onChange={(_, data) => {
                  setBaselineId(data.value)
                  setComparison(null)
                }}
              >
                <option value="">Select a baseline</option>
                {baselines.map((group) => (
                  <option key={group.id} value={group.id}>
                    {group.display_group} · {group.atomic_attack_name} · {group.id}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Attack group" className={styles.selector}>
              <Select
                value={attackId}
                onChange={(_, data) => {
                  setAttackId(data.value)
                  setComparison(null)
                }}
              >
                <option value="">Select an attack</option>
                {attacks.map((group) => (
                  <option key={group.id} value={group.id}>
                    {group.display_group} · {group.atomic_attack_name} · {group.id}
                  </option>
                ))}
              </Select>
            </Field>
            <Button
              appearance="primary"
              disabled={!validSelection}
              onClick={() => {
                setComparison((current) => ({ baselineId, attackId, version: (current?.version ?? 0) + 1 }))
              }}
            >
              Compare outcomes
            </Button>
          </div>
          {validSelection && comparison && comparison.baselineId === baselineId && comparison.attackId === attackId && (
            <Comparison
              key={`${scenarioResultId}:${comparison.version}:${baselineId}:${attackId}`}
              scenarioResultId={scenarioResultId}
              baselineId={baselineId}
              attackId={attackId}
            />
          )}
        </>
      )}
    </section>
  )
}

/** A new selection or refresh mounts a new request, clearing previous results immediately. */
function Comparison({ scenarioResultId, baselineId, attackId }: ComparisonProps) {
  const styles = usePairedOutcomesStyles()
  const [state, setState] = useState<
    { status: 'loading' }
    | { status: 'error'; error: string }
    | { status: 'ready'; report: PairedOutcomeStatistics }
  >({ status: 'loading' })

  useEffect(() => {
    const controller = new AbortController()
    void scenariosApi.getPairedOutcomes(scenarioResultId, {
      baseline_group_id: baselineId,
      attack_group_id: attackId,
    }, controller.signal).then((report) => {
      if (!controller.signal.aborted) setState({ status: 'ready', report })
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setState({ status: 'error', error: toApiError(error).detail })
    })
    return () => controller.abort()
  }, [scenarioResultId, baselineId, attackId])

  if (state.status === 'loading') return <Spinner size="small" label="Comparing outcomes" />
  if (state.status === 'error') {
    return <MessageBar intent="error" layout="multiline" className={styles.errorMessage}>
      <MessageBarBody>{state.error}</MessageBarBody>
    </MessageBar>
  }

  const report = state.report
  const difference = report.paired_difference === null
    ? 'Unavailable'
    : `${report.paired_difference > 0 ? '+' : ''}${(report.paired_difference * 100).toFixed(1)} pp`
  const exclusions = [
    ['Missing baseline', report.missing_baseline],
    ['Missing attack', report.missing_attack],
    ['Both missing', report.missing_both],
    ['Baseline errors', report.baseline_errors],
    ['Attack errors', report.attack_errors],
    ['Baseline undetermined', report.baseline_undetermined],
    ['Attack undetermined', report.attack_undetermined],
    ['Unmatched baseline seeds', report.unmatched_baseline_seeds],
    ['Unmatched attack seeds', report.unmatched_attack_seeds],
    ['Unattributed attempts', report.unattributed_attempts],
  ] as const

  return (
    <section className={styles.report} aria-label="Paired comparison" aria-live="polite">
      <div className={styles.metrics}>
        <Metric label="Baseline success rate" value={rate(report.baseline_success_rate)} />
        <Metric label="Attack success rate" value={rate(report.attack_success_rate)} />
        <Metric label="Paired difference" value={difference} />
        <Metric
          label="Decided pairs"
          value={`${report.decided_pairs.toLocaleString()} / ${report.planned_pairs.toLocaleString()}`}
        />
        <Metric label="Excluded pairs" value={report.excluded_pairs.toLocaleString()} />
      </div>
      <Text>Rates use the same decided pairs. Difference = attack − baseline, in percentage points (pp).</Text>
      <Table aria-label="Outcome transitions">
        <TableHeader>
          <TableRow>
            <TableHeaderCell>Baseline → Attack</TableHeaderCell>
            <TableHeaderCell>Pairs</TableHeaderCell>
          </TableRow>
        </TableHeader>
        <TableBody>
          {TRANSITIONS.map(({ label, id }) => (
            <TransitionRow
              key={id}
              label={label}
              transition={id}
              count={report[id]}
              scenarioResultId={scenarioResultId}
              baselineId={baselineId}
              attackId={attackId}
            />
          ))}
        </TableBody>
      </Table>
      <Text weight="semibold">Exclusion details</Text>
      <div className={styles.metrics}>
        {exclusions.map(([label, count]) => <Metric key={label} label={label} value={count.toLocaleString()} />)}
      </div>
      <Text size={200}>
        Planned pairs share a logical seed ID. Unmatched seeds are separate.
        Reasons can overlap; do not sum them to reconstruct exclusions.
      </Text>
      <Text size={200}>
        Only the latest top-level outcome counts; child attempts and older retries do not add pairs.
        Missing scoring metadata limits compatibility checks. These observed differences do not establish causation.
      </Text>
    </section>
  )
}

interface TransitionRowProps extends ComparisonProps {
  readonly label: string
  readonly transition: PairedOutcomeTransition
  readonly count: number
}

function TransitionRow({ label, transition, count, scenarioResultId, baselineId, attackId }: TransitionRowProps) {
  const styles = usePairedOutcomesStyles()
  const detailsId = useId()
  const [expanded, setExpanded] = useState(false)

  return (
    <>
      <TableRow>
        <TableCell>
          <Button
            appearance="subtle"
            className={styles.expandButton}
            aria-expanded={expanded}
            aria-controls={detailsId}
            onClick={() => setExpanded((current) => !current)}
          >
            <span aria-hidden="true" className={styles.chevron}>{expanded ? '▾' : '▸'}</span>
            {label}
          </Button>
        </TableCell>
        <TableCell>{count.toLocaleString()}</TableCell>
      </TableRow>
      {expanded && (
        <TableRow>
          <TableCell colSpan={2} className={styles.detailsCell}>
            <div id={detailsId}>
              <TransitionDetails
                label={label}
                transition={transition}
                count={count}
                scenarioResultId={scenarioResultId}
                baselineId={baselineId}
                attackId={attackId}
              />
            </div>
          </TableCell>
        </TableRow>
      )}
    </>
  )
}

interface DetailState {
  pairs: PairedOutcomeDetail[]
  pageStart: number
  nextCursor: string | null
  status: 'loading' | 'ready' | 'error'
  error?: string
}

function TransitionDetails({ label, transition, count, scenarioResultId, baselineId, attackId }: TransitionRowProps) {
  const styles = usePairedOutcomesStyles()
  const [state, setState] = useState<DetailState>({ pairs: [], pageStart: 0, nextCursor: null, status: 'loading' })
  const [request, setRequest] = useState<{ cursor?: string; version: number }>({ version: 0 })
  const detailsRegion = useRef<HTMLElement>(null)
  const firstNewPair = useRef<HTMLElement>(null)

  useEffect(() => {
    if (count === 0) return
    const controller = new AbortController()
    void scenariosApi.getPairedOutcomeDetails(scenarioResultId, {
      baseline_group_id: baselineId,
      attack_group_id: attackId,
      transition,
      limit: DETAIL_PAGE_SIZE,
      ...(request.cursor ? { cursor: request.cursor } : {}),
    }, controller.signal).then((page) => {
      if (controller.signal.aborted) return
      setState((previous) => {
        if (page.total_pairs !== count || page.baseline_group_id !== baselineId
          || page.attack_group_id !== attackId || page.transition !== transition
          || (page.next_cursor && page.next_cursor === request.cursor)) {
          return { ...previous, status: 'error', error: 'Pair details changed. Compare outcomes again to refresh.' }
        }
        const pairs = [...previous.pairs, ...page.pairs]
        if (pairs.length > count || new Set(pairs.map((pair) => pair.seed_group_id)).size !== pairs.length
          || (page.next_cursor === null && pairs.length !== count)
          || (page.next_cursor !== null && (page.pairs.length === 0 || pairs.length === count))) {
          return { ...previous, status: 'error', error: 'Pair details could not be paged consistently. Compare outcomes again.' }
        }
        return { pairs, pageStart: previous.pairs.length, nextCursor: page.next_cursor, status: 'ready' }
      })
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) {
        setState((previous) => ({ ...previous, status: 'error', error: toApiError(error).detail }))
      }
    })
    return () => controller.abort()
  }, [scenarioResultId, baselineId, attackId, transition, count, request])

  useEffect(() => {
    if (state.status === 'ready' && document.activeElement === detailsRegion.current) {
      // Restore focus lost with the loading button, without interrupting navigation elsewhere.
      firstNewPair.current?.focus()
    }
  }, [state.status, state.pairs])

  const fetchPage = (cursor?: string) => {
    if (document.activeElement instanceof HTMLButtonElement) detailsRegion.current?.focus({ preventScroll: true })
    setState((previous) => ({ ...previous, status: 'loading', error: undefined }))
    setRequest((previous) => ({ cursor, version: previous.version + 1 }))
  }

  return (
    <section ref={detailsRegion} tabIndex={-1} aria-label={`${label} pair details`} className={styles.details} aria-live="polite">
      {count === 0 && <Text>No matching pairs in this transition.</Text>}
      {state.pairs.map((pair, position) => (
        <article
          key={pair.seed_group_id}
          className={styles.pairCard}
          ref={position === state.pageStart ? firstNewPair : undefined}
          tabIndex={-1}
        >
          <div className={styles.pairHeader}>
            <Text weight="semibold" block>Objective</Text>
            <Text className={styles.wrappingText} block>{pair.objective || 'Objective unavailable'}</Text>
            <Text size={200} className={styles.wrappingText} block>Logical seed: {pair.seed_group_id}</Text>
          </div>
          {pair.seed_prompts.length > 0 && (
            <div className={styles.seedPrompts}>
              <Text weight="semibold" block>Seed prompts</Text>
              {/* Multimodal pieces share sequences; positions are fixed in this saved prompt list. */}
              {pair.seed_prompts.map((prompt, position) => (
                <div key={`${prompt.sequence}:${position}`} className={styles.wrappingText}>
                  <Text size={200} block>
                    {`#${prompt.sequence}${prompt.role ? ` · ${prompt.role}` : ''}${prompt.data_type ? ` · ${prompt.data_type}` : ''}`}
                  </Text>
                  <Text block className={styles.promptValue}>{prompt.value}</Text>
                </div>
              ))}
            </div>
          )}
          <div className={styles.sides}>
            <OutcomeSide label="Baseline" side={pair.baseline} scenarioResultId={scenarioResultId} />
            <OutcomeSide label="Attack" side={pair.attack} scenarioResultId={scenarioResultId} />
          </div>
        </article>
      ))}
      {state.status === 'loading' && count > 0 && (
        <Spinner size="small" label={state.pairs.length ? `Loading more ${label} pairs` : `Loading ${label} pairs`} />
      )}
      {state.status === 'error' && (
        <div className={styles.feedback}>
          <MessageBar intent="error" layout="multiline" className={styles.errorMessage}>
            <MessageBarBody>{state.error}</MessageBarBody>
          </MessageBar>
          <Button onClick={() => fetchPage(request.cursor)}>{`Retry ${label} pairs`}</Button>
        </div>
      )}
      {state.status === 'ready' && (
        <div className={styles.pageControls}>
          <Text size={200}>{`${state.pairs.length} of ${count.toLocaleString()} pairs shown`}</Text>
          {state.nextCursor && (
            <Button onClick={() => fetchPage(state.nextCursor ?? undefined)}>{`Load more ${label} pairs`}</Button>
          )}
        </div>
      )}
    </section>
  )
}

function OutcomeSide({ label, side, scenarioResultId }: {
  label: string
  side: PairedOutcomeDetailSide
  scenarioResultId: string
}) {
  const styles = usePairedOutcomesStyles()
  return (
    <div className={styles.side}>
      <Text weight="semibold" block>{label}</Text>
      <Text block>{`Outcome: ${side.outcome}`}</Text>
      {side.outcome_reason && <Text block className={styles.wrappingText}>{side.outcome_reason}</Text>}
      <Text size={200} block>{new Date(side.timestamp).toLocaleString()}</Text>
      {side.attack_result_id && side.conversation_id ? (
        <Link
          className={styles.conversationLink}
          to={attackConversationRoutePath(side.attack_result_id, side.conversation_id, scenarioResultId)}
        >
          {`Open ${label.toLowerCase()} conversation`}
        </Link>
      ) : side.child_conversation ? (
        <>
          <Text size={200} block>{`Linked child outcome: ${side.child_conversation.outcome}`}</Text>
          <Link
            className={styles.conversationLink}
            to={attackConversationRoutePath(
              side.child_conversation.attack_result_id, side.child_conversation.conversation_id, scenarioResultId,
            )}
          >
            {`Open ${label.toLowerCase()} child conversation`}
          </Link>
        </>
      ) : <Text size={200}>Conversation unavailable</Text>}
    </div>
  )
}

function rate(value: number | null): string {
  return value === null ? 'Unavailable' : `${(value * 100).toFixed(1)}%`
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div role="group" aria-label={label}>
      <Text size={200} block>{label}</Text>
      <Text size={500} weight="semibold">{value}</Text>
    </div>
  )
}
