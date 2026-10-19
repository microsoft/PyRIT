import { useEffect, useId, useState } from 'react'

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
import type { PairedOutcomeStatistics, ScenarioRunPlan, ScenarioRunState } from '@/types'

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
    return <MessageBar intent="error"><MessageBarBody>{state.error}</MessageBarBody></MessageBar>
  }

  const report = state.report
  const difference = report.paired_difference === null
    ? 'Unavailable'
    : `${report.paired_difference > 0 ? '+' : ''}${(report.paired_difference * 100).toFixed(1)} pp`
  const transitions = [
    ['Failure → Success', report.failure_to_success],
    ['Success → Failure', report.success_to_failure],
    ['Failure → Failure', report.failure_to_failure],
    ['Success → Success', report.success_to_success],
  ] as const
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
          {transitions.map(([label, count]) => (
            <TableRow key={label}>
              <TableCell>{label}</TableCell>
              <TableCell>{count.toLocaleString()}</TableCell>
            </TableRow>
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
