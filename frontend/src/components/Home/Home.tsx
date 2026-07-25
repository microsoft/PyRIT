import { useEffect, useMemo, useState } from 'react'
import {
  Badge,
  Button,
  MessageBar,
  MessageBarBody,
  Spinner,
  Text,
  tokens,
} from '@fluentui/react-components'
import {
  ArrowRightRegular,
  CheckmarkCircleRegular,
  DismissCircleRegular,
  ErrorCircleRegular,
  QuestionCircleRegular,
  TagMultipleRegular,
  TargetRegular,
} from '@fluentui/react-icons'
import LabelsBar from '../Labels/LabelsBar'
import { attacksApi } from '../../services/api'
import { toApiError } from '../../services/errors'
import type { AttackSummary, TargetInstance } from '../../types'
import { targetEndpoint, targetModelName, targetType } from '../../utils/targetIdentity'
import type { ViewName } from '../Sidebar/Navigation'
import { useHomeStyles } from './Home.styles'

const RECENT_ATTACKS_LIMIT = 50
const MAX_OPERATIONS = 5
const MAX_ATTACKS_PER_OPERATION = 3
const NO_OPERATION_KEY = '__no_operation__'

const OUTCOME_ICONS: Record<string, React.ReactElement> = {
  success: <CheckmarkCircleRegular style={{ color: tokens.colorPaletteGreenForeground1 }} />,
  failure: <DismissCircleRegular style={{ color: tokens.colorPaletteRedForeground1 }} />,
  error: <ErrorCircleRegular style={{ color: tokens.colorPaletteRedForeground1 }} />,
  undetermined: <QuestionCircleRegular style={{ color: tokens.colorNeutralForeground3 }} />,
}

interface HomeProps {
  labels: Record<string, string>
  onLabelsChange: (labels: Record<string, string>) => void
  activeTarget: TargetInstance | null
  onNavigate: (view: ViewName) => void
  onOpenAttack: (attackResultId: string) => void
}

interface OperationGroup {
  name: string
  isUnlabeled: boolean
  attacks: AttackSummary[]
  lastActivity: number
}

function groupAttacksByOperation(attacks: AttackSummary[]): OperationGroup[] {
  const groups = new Map<string, OperationGroup>()

  for (const attack of attacks) {
    const opLabel = attack.labels?.operation
    const isUnlabeled = !opLabel
    const key = isUnlabeled ? NO_OPERATION_KEY : opLabel
    const updatedAt = new Date(attack.updated_at).getTime()

    const existing = groups.get(key)
    if (existing) {
      existing.attacks.push(attack)
      if (updatedAt > existing.lastActivity) {
        existing.lastActivity = updatedAt
      }
    } else {
      groups.set(key, {
        name: isUnlabeled ? '(no operation)' : opLabel,
        isUnlabeled,
        attacks: [attack],
        lastActivity: updatedAt,
      })
    }
  }

  return Array.from(groups.values()).sort((a, b) => b.lastActivity - a.lastActivity)
}

function formatRelativeTime(iso: string): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ''
  const diffMs = Date.now() - then
  const seconds = Math.round(diffMs / 1000)
  if (seconds < 60) return 'just now'
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.round(hours / 24)
  if (days < 7) return `${days}d ago`
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

function targetDisplayName(target: TargetInstance): string {
  return targetModelName(target) || target.target_registry_name || targetType(target)
}

export default function Home({
  labels,
  onLabelsChange,
  activeTarget,
  onNavigate,
  onOpenAttack,
}: HomeProps) {
  const styles = useHomeStyles()
  const [attacks, setAttacks] = useState<AttackSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let ignore = false
    attacksApi
      .listAttacks({ limit: RECENT_ATTACKS_LIMIT })
      .then(resp => {
        if (ignore) return
        setAttacks(resp.items.map(item => ({ ...item, labels: item.labels ?? {} })))
        setError(null)
      })
      .catch(err => {
        if (ignore) return
        setAttacks([])
        setError(toApiError(err).detail)
      })
      .finally(() => {
        if (!ignore) setLoading(false)
      })
    return () => { ignore = true }
  }, [])

  const operations = useMemo(
    () => groupAttacksByOperation(attacks).slice(0, MAX_OPERATIONS),
    [attacks],
  )

  return (
    <div className={styles.root} data-testid="home-view">
      <div className={styles.container}>
        <div className={styles.hero}>
          <Text as="h1" size={700} weight="semibold" className={styles.heroTitle}>
            Welcome to Co-PyRIT
          </Text>
          <Text size={300} className={styles.heroSubtitle}>
            Set your labels, pick a target, and start a new attack — or jump back into a recent operation.
          </Text>
        </div>

        <div className={styles.setupGrid}>
          <section className={styles.card} data-testid="home-labels-card" data-tour="labels-card">
            <div className={styles.cardHeader}>
              <span className={styles.cardIcon}><TagMultipleRegular /></span>
              <Text as="h2" size={500} weight="semibold">Labels</Text>
            </div>
            <div className={styles.cardBody}>
              <Text size={200} className={styles.heroSubtitle}>
                Labels (especially <strong>operator</strong> and <strong>operation</strong>) are stored on
                every attack so you can find them later. Update the placeholders before you run anything real.
              </Text>
              <div className={styles.labelsRow}>
                <LabelsBar labels={labels} onLabelsChange={onLabelsChange} />
              </div>
            </div>
          </section>

          <section className={styles.card} data-testid="home-target-card" data-tour="target-card">
            <div className={styles.cardHeader}>
              <span className={styles.cardIcon}><TargetRegular /></span>
              <Text as="h2" size={500} weight="semibold">Target</Text>
            </div>
            <div className={styles.cardBody}>
              {activeTarget ? (
                <div className={styles.targetSummary} data-testid="home-target-active">
                  <Text className={styles.targetName}>{targetDisplayName(activeTarget)}</Text>
                  <Text size={200} className={styles.targetMeta}>
                    {targetType(activeTarget)}
                    {targetEndpoint(activeTarget) ? ` · ${targetEndpoint(activeTarget)}` : ''}
                  </Text>
                </div>
              ) : (
                <Text size={300} className={styles.emptyHint} data-testid="home-target-empty">
                  No target selected. Pick one to send prompts.
                </Text>
              )}
            </div>
            <div className={styles.cardFooter}>
              <Button
                appearance="primary"
                icon={<ArrowRightRegular />}
                iconPosition="after"
                onClick={() => onNavigate('config')}
                data-testid="home-configure-target-btn"
                className={styles.mobilePrimaryAction}
              >
                {activeTarget ? 'Manage targets' : 'Configure a target'}
              </Button>
            </div>
          </section>
        </div>

        <section data-testid="home-recent-operations">
          <div className={styles.sectionHeader}>
            <Text as="h2" size={500} weight="semibold">Recent operations</Text>
            <Button
              appearance="subtle"
              icon={<ArrowRightRegular />}
              iconPosition="after"
              onClick={() => onNavigate('history')}
              data-testid="home-view-all-history-btn"
            >
              View all history
            </Button>
          </div>

          {loading ? (
            <div className={styles.loadingState} data-testid="home-loading">
              <Spinner size="medium" label="Loading recent operations..." />
            </div>
          ) : error ? (
            <MessageBar intent="error" data-testid="home-error">
              <MessageBarBody>{error}</MessageBarBody>
            </MessageBar>
          ) : operations.length === 0 ? (
            <div className={styles.emptyOperations} data-testid="home-empty">
              <Text size={400}>No attacks yet</Text>
              <Text size={200}>
                Configure a target and start a new attack from the Chat tab.
              </Text>
              <Button
                appearance="primary"
                onClick={() => onNavigate('chat')}
                data-testid="home-start-attack-btn"
              >
                Go to chat
              </Button>
            </div>
          ) : (
            <div className={styles.operationsGrid}>
              {operations.map(op => {
                const visibleAttacks = op.attacks.slice(0, MAX_ATTACKS_PER_OPERATION)
                const hiddenCount = op.attacks.length - visibleAttacks.length
                const key = op.isUnlabeled ? NO_OPERATION_KEY : op.name
                return (
                  <div
                    key={key}
                    className={styles.operationCard}
                    data-testid={`home-operation-${op.isUnlabeled ? 'unlabeled' : op.name}`}
                  >
                    <div className={styles.operationHeader}>
                      <Text as="h3" size={400} className={styles.operationName} title={op.name}>
                        {op.name}
                      </Text>
                      <Badge appearance="tint" size="small">
                        {op.attacks.length} {op.attacks.length === 1 ? 'attack' : 'attacks'}
                      </Badge>
                    </div>
                    <Text size={200} className={styles.operationMeta}>
                      Last activity {formatRelativeTime(new Date(op.lastActivity).toISOString())}
                    </Text>
                    <div className={styles.attackList}>
                      {visibleAttacks.map(attack => (
                        <button
                          key={attack.attack_result_id}
                          type="button"
                          className={styles.attackRow}
                          onClick={() => onOpenAttack(attack.attack_result_id)}
                          data-testid={`home-open-attack-${attack.attack_result_id}`}
                          title={attack.last_message_preview || attack.attack_type}
                        >
                          {OUTCOME_ICONS[attack.outcome ?? 'undetermined'] ?? OUTCOME_ICONS.undetermined}
                          <Text size={200} className={styles.attackPreview}>
                            {attack.last_message_preview || attack.attack_type}
                          </Text>
                          <Text className={styles.attackTimestamp}>
                            {formatRelativeTime(attack.updated_at)}
                          </Text>
                        </button>
                      ))}
                      {hiddenCount > 0 && (
                        <Text size={200} className={styles.operationMeta}>
                          +{hiddenCount} more in history
                        </Text>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
