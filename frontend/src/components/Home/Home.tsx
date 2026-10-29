import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { useNavigate } from 'react-router'
import {
  Badge,
  Button,
  MessageBar,
  MessageBarBody,
  Spinner,
  Tab,
  TabList,
  Text,
} from '@fluentui/react-components'
import type { BadgeProps } from '@fluentui/react-components'
import {
  AddRegular,
  ArrowClockwiseRegular,
  ArrowRightRegular,
  BriefcaseRegular,
  ChatRegular,
  ChevronRightRegular,
  HistoryRegular,
  ScriptRegular,
  TargetRegular,
} from '@fluentui/react-icons'

import OutcomeBadge from '@/components/OutcomeBadge'
import type { ViewName } from '@/components/Sidebar/Navigation'
import { useRuntime } from '@/hooks/useRuntime'
import { attacksApi, operationsApi, scenariosApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { AttackSummary, OperationListItem, ScenarioRunListItem, ScenarioRunState } from '@/types'
import { scenarioRunRoutePath } from '@/utils/routeParams'

import { useHomeStyles } from './Home.styles'

const RECENT_WORK_LIMIT = 5
const VISIBLE_OPERATIONS_LIMIT = 3
const SCAN_STATUS_LABELS: Record<ScenarioRunState, string> = {
  CREATED: 'Created', QUEUED: 'Queued', IN_PROGRESS: 'Executing',
  COMPLETED: 'Completed', FAILED: 'Failed', CANCELLED: 'Cancelled',
}
const SCAN_STATUS_COLORS: Record<ScenarioRunState, BadgeProps['color']> = {
  CREATED: 'informative', QUEUED: 'informative', IN_PROGRESS: 'brand',
  COMPLETED: 'success', FAILED: 'danger', CANCELLED: 'warning',
}

interface HomeProps {
  readonly onNavigate: (view: ViewName) => void
  readonly onNewAttack: () => void
  readonly onOpenAttack: (attackResultId: string) => void
  readonly onOpenAttackHistory: () => void
  readonly onOpenScannerHistory: () => void
}

interface LoadedSummaries<T> {
  readonly key: string
  readonly items: T[]
  readonly error: string | null
}

interface RecentItem {
  readonly id: string
  readonly kind: 'Attack' | 'Scan'
  readonly title: string
  readonly operationName?: string | null
  readonly updatedAt: string
  readonly badge: ReactNode
  readonly open: () => void
}

type RecentFilter = 'all' | 'attacks' | 'scans'

function activityTime(timestamp: string): number {
  const parsed = Date.parse(timestamp)
  return Number.isNaN(parsed) ? 0 : parsed
}

function formatRelativeTime(timestamp: string): string {
  const parsed = Date.parse(timestamp)
  if (Number.isNaN(parsed)) return 'Time unavailable'
  const minutes = Math.floor(Math.max(0, Date.now() - parsed) / 60_000)
  if (minutes < 1) return 'Just now'
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days < 7) return `${days}d ago`
  return new Date(timestamp).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

export default function Home({
  onNavigate, onNewAttack, onOpenAttack, onOpenAttackHistory, onOpenScannerHistory,
}: HomeProps) {
  const styles = useHomeStyles()
  const navigate = useNavigate()
  const { generation, ready } = useRuntime()
  const [filter, setFilter] = useState<RecentFilter>('all')
  const [revision, setRevision] = useState(0)
  const [attackResult, setAttackResult] = useState<LoadedSummaries<AttackSummary> | null>(null)
  const [scanResult, setScanResult] = useState<LoadedSummaries<ScenarioRunListItem> | null>(null)
  const [operationResult, setOperationResult] = useState<LoadedSummaries<OperationListItem> | null>(null)
  const requestKey = `${generation}:${revision}`

  useEffect(() => {
    if (!ready) return
    let ignore = false
    const load = <T,>(
      request: Promise<{ items: T[] }>,
      commit: (result: LoadedSummaries<T>) => void,
    ): void => {
      request.then((response: { items: T[] }) => {
        if (!ignore) commit({ key: requestKey, items: response.items, error: null })
      }).catch((cause: unknown) => {
        if (!ignore) commit({ key: requestKey, items: [], error: toApiError(cause).detail })
      })
    }
    load(attacksApi.listAttacks({ limit: RECENT_WORK_LIMIT, include_scenario_attacks: false }), setAttackResult)
    load(scenariosApi.listRuns({ limit: RECENT_WORK_LIMIT }), setScanResult)
    load(operationsApi.list(), setOperationResult)
    return () => { ignore = true }
  }, [requestKey, ready])

  const attacksLoading = !ready || attackResult?.key !== requestKey
  const scansLoading = !ready || scanResult?.key !== requestKey
  const operationsLoading = !ready || operationResult?.key !== requestKey
  const attacks = !attacksLoading && attackResult ? attackResult.items : []
  const scans = !scansLoading && scanResult ? scanResult.items : []
  const operations = !operationsLoading && operationResult
    ? operationResult.items.slice(0, VISIBLE_OPERATIONS_LIMIT) : []
  const recentLoading = (filter !== 'scans' && attacksLoading) || (filter !== 'attacks' && scansLoading)
  const recentErrors = [
    filter !== 'scans' && !attacksLoading && attackResult?.error
      ? `Could not load recent attacks: ${attackResult.error}` : null,
    filter !== 'attacks' && !scansLoading && scanResult?.error
      ? `Could not load recent scans: ${scanResult.error}` : null,
  ].filter((error: string | null): error is string => error !== null)
  const operationsError = !operationsLoading ? operationResult?.error : null
  const recent: RecentItem[] = [
    ...attacks.map((attack: AttackSummary): RecentItem => ({
      id: attack.attack_result_id,
      kind: 'Attack',
      title: attack.objective || attack.last_message_preview || attack.attack_type,
      operationName: attack.operation_name,
      updatedAt: attack.updated_at,
      badge: <OutcomeBadge outcome={attack.outcome} appearance="tint" />,
      open: () => { onOpenAttack(attack.attack_result_id) },
    })),
    ...scans.map((scan: ScenarioRunListItem): RecentItem => ({
      id: scan.scenario_result_id,
      kind: 'Scan',
      title: scan.scenario_registry_name || scan.scenario_name,
      operationName: scan.operation_name,
      updatedAt: scan.updated_at,
      badge: <Badge appearance="tint" color={SCAN_STATUS_COLORS[scan.status]}>{SCAN_STATUS_LABELS[scan.status]}</Badge>,
      open: () => { void navigate(scenarioRunRoutePath(scan.scenario_result_id)) },
    })),
  ].filter((item: RecentItem) => filter === 'all' || (filter === 'attacks' ? item.kind === 'Attack' : item.kind === 'Scan'))
    .sort((first: RecentItem, second: RecentItem) => activityTime(second.updatedAt) - activityTime(first.updatedAt))
    .slice(0, RECENT_WORK_LIMIT)
  const refresh = (): void => { setRevision((value: number) => value + 1) }

  return (
    <div className={styles.root} data-testid="home-view">
      <div className={styles.container}>
        <header className={styles.sectionHeader}>
          <div className={styles.hero}>
            <Text as="h1" size={800} weight="semibold" className={styles.heroTitle}>Home</Text>
            <Text size={400} className={styles.secondary}>
              Start an attack, configure a scan, or return to recent work.
            </Text>
          </div>
          <Button appearance="subtle" icon={<ArrowClockwiseRegular />} onClick={refresh} className={styles.touchTarget}>
            Refresh Home
          </Button>
        </header>

        <div className={styles.workflowGrid} data-tour="home-workflows">
          <section className={styles.workflowCard} aria-labelledby="home-attacks-title">
            <div className={styles.workflowHeading}>
              <span className={styles.workflowIcon} aria-hidden="true"><ChatRegular /></span>
              <Text as="h2" id="home-attacks-title" size={600} weight="semibold">Attacks</Text>
            </div>
            <Text className={styles.secondary}>Explore a target through a conversation. Try prompts and review responses.</Text>
            <div className={styles.cardActions}>
              <Button appearance="primary" icon={<AddRegular />} onClick={onNewAttack} className={styles.touchTarget}>
                New attack
              </Button>
              <Button appearance="subtle" icon={<ArrowRightRegular />} iconPosition="after"
                onClick={onOpenAttackHistory} className={styles.touchTarget}>Attack history</Button>
            </div>
          </section>
          <section className={styles.workflowCard} aria-labelledby="home-scans-title">
            <div className={styles.workflowHeading}>
              <span className={styles.workflowIcon} aria-hidden="true"><TargetRegular /></span>
              <Text as="h2" id="home-scans-title" size={600} weight="semibold">Scans</Text>
            </div>
            <Text className={styles.secondary}>Evaluate targets with automated attack techniques and datasets.</Text>
            <div className={styles.cardActions}>
              <Button appearance="secondary" icon={<AddRegular />} className={styles.touchTarget}
                onClick={() => { onNavigate('scenarios') }}>Configure scan</Button>
              <Button appearance="subtle" icon={<ArrowRightRegular />} iconPosition="after"
                onClick={onOpenScannerHistory} className={styles.touchTarget}>Scan history</Button>
            </div>
          </section>
        </div>

        <section aria-labelledby="home-recent-title" aria-busy={recentLoading}>
          <div className={styles.sectionHeader}>
            <Text as="h2" id="home-recent-title" size={500} weight="semibold">Recent work</Text>
            <TabList size="small" selectedValue={filter} aria-label="Recent work type"
              onTabSelect={(_, data) => {
                if (data.value === 'all' || data.value === 'attacks' || data.value === 'scans') setFilter(data.value)
              }}>
              <Tab value="all" className={styles.touchTarget}>All</Tab>
              <Tab value="attacks" className={styles.touchTarget}>Attacks</Tab>
              <Tab value="scans" className={styles.touchTarget}>Scans</Tab>
            </TabList>
          </div>
          {recentErrors.map((error: string) => (
            <MessageBar key={error} intent="error"><MessageBarBody>
              {error} <Button onClick={refresh} className={styles.touchTarget}>Retry recent work</Button>
            </MessageBarBody></MessageBar>
          ))}
          {recentLoading && <Spinner size="small" label="Loading recent work..." />}
          {recent.length > 0 && (
            <ul className={styles.recentList}>
              {recent.map((item: RecentItem) => (
                <li key={`${item.kind}:${item.id}`}>
                  <button type="button" className={styles.recentRow} onClick={item.open}
                    title={item.title} data-testid={`home-open-${item.kind.toLowerCase()}-${item.id}`}>
                    <span className={styles.rowIcon} aria-hidden="true">
                      {item.kind === 'Attack' ? <ChatRegular /> : <TargetRegular />}
                    </span>
                    <span className={styles.rowContent}>
                      <Text weight="semibold" className={styles.rowTitle}>{item.title}</Text>
                      <span className={styles.rowMeta}>
                        <Text size={200}>{item.kind}</Text>
                        {item.operationName && <><span aria-hidden="true">·</span><Text size={200}>{item.operationName}</Text></>}
                      </span>
                    </span>
                    <span className={styles.status}>{item.badge}</span>
                    <time dateTime={item.updatedAt} className={styles.timestamp}>
                      {formatRelativeTime(item.updatedAt)}
                    </time>
                    <ChevronRightRegular className={styles.rowChevron} aria-hidden="true" />
                  </button>
                </li>
              ))}
            </ul>
          )}
          {!recentLoading && recentErrors.length === 0 && recent.length === 0 && (
            <div className={styles.emptyState}>
              <HistoryRegular className={styles.emptyIcon} aria-hidden="true" />
              <Text size={400} weight="semibold">
                {filter === 'all' ? 'Your recent work will appear here' : filter === 'attacks' ? 'No recent attacks' : 'No recent scans'}
              </Text>
              <Text className={styles.secondary}>Start with an attack or configure a scan above.</Text>
            </div>
          )}
        </section>

        <section aria-labelledby="home-operations-title" aria-busy={operationsLoading}>
          <div className={styles.sectionHeader}>
            <Text as="h2" id="home-operations-title" size={500} weight="semibold">Operations</Text>
            <Button appearance="subtle" icon={<ArrowRightRegular />} iconPosition="after" className={styles.touchTarget}
              onClick={() => { onNavigate('operations') }}>View operations</Button>
          </div>
          {operationsLoading ? <Spinner size="small" label="Loading operations..." /> : operationsError ? (
            <MessageBar intent="error"><MessageBarBody>
              Could not load operations: {operationsError}{' '}
              <Button onClick={refresh} className={styles.touchTarget}>Retry operations</Button>
            </MessageBarBody></MessageBar>
          ) : operations.length > 0 ? (
            <div className={styles.operationsGrid}>
              {operations.map((operation: OperationListItem) => (
                <button key={operation.id} type="button" className={styles.operationCard} title={operation.name}
                  onClick={() => { void navigate(`/operations/${encodeURIComponent(operation.id)}`) }}>
                  <BriefcaseRegular className={styles.rowIcon} aria-hidden="true" />
                  <span className={styles.rowContent}>
                    <Text weight="semibold" className={styles.rowTitle}>{operation.name}</Text>
                    {operation.description && <Text size={200} className={styles.operationDescription}>{operation.description}</Text>}
                  </span>
                  <ChevronRightRegular className={styles.rowChevron} aria-hidden="true" />
                </button>
              ))}
            </div>
          ) : (
            <div className={styles.emptyOperations}>
              <BriefcaseRegular className={styles.rowIcon} aria-hidden="true" />
              <div className={styles.rowContent}>
                <Text weight="semibold">Keep related work together</Text>
                <Text size={200} className={styles.secondary}>Use operations to organize attacks, scans, and findings.</Text>
              </div>
              <Button onClick={() => { onNavigate('operations') }} className={styles.touchTarget}>Open operations</Button>
            </div>
          )}
        </section>

        <footer className={styles.setupFooter}>
          <ScriptRegular aria-hidden="true" />
          <Text size={200} className={styles.secondary}>Need to set up a target or technique?</Text>
          <Button size="small" appearance="subtle" icon={<ArrowRightRegular />} iconPosition="after" className={styles.touchTarget}
            onClick={() => { onNavigate('registry') }}>Open Registry</Button>
        </footer>
      </div>
    </div>
  )
}
