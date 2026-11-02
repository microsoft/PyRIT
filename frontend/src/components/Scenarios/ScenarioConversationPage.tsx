import { useCallback, useEffect, useState } from 'react'

import { Badge, Button, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle, MessageBar, MessageBarBody, Spinner, Text } from '@fluentui/react-components'
import { ArrowLeftRegular, StopRegular } from '@fluentui/react-icons'
import { Link, useNavigate, useParams } from 'react-router'

import MessageList from '@/components/Chat/MessageList'
import { useConversationMessages } from '@/hooks/useConversationMessages'
import { SCENARIO_RUN_POLL_INTERVAL_MS, useScenarioRunProgress } from '@/hooks/useScenarioRunProgress'
import { scenariosApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { ScenarioActiveConversation } from '@/types'
import { attackConversationRoutePath, routerPathParamValue, scenarioRunRoutePath } from '@/utils/routeParams'
import { isTerminalRunState } from '@/utils/scenarioRunProgress'

import { useScenarioRunPageStyles } from './ScenarioRunPage.styles'

const NO_SENDS: ReadonlySet<string> = new Set()
const noSendInFlight = (): boolean => false

export default function ScenarioConversationPage() {
  const { scenarioResultId, conversationId } = useParams<{ scenarioResultId: string; conversationId: string }>()
  return <ScanConversation key={`${scenarioResultId}:${conversationId}`}
    scenarioResultId={routerPathParamValue(scenarioResultId)} conversationId={routerPathParamValue(conversationId)} />
}

interface ScanConversationProps {
  readonly scenarioResultId: string
  readonly conversationId: string
}

function ScanConversation({ scenarioResultId, conversationId }: ScanConversationProps) {
  const styles = useScenarioRunPageStyles()
  const navigate = useNavigate()
  const { state, applyRunSummary, retry } = useScenarioRunProgress(scenarioResultId)
  const active = state.activeConversations.find((conversation) => conversation.conversation_id === conversationId)
  const saved = state.results.find((attempt) => attempt.conversation_id === conversationId || attempt.related_conversation_ids?.includes(conversationId))
  const recorded = state.recordedConversations.find((conversation) => conversation.conversation_id === conversationId)
  const [lastActive, setLastActive] = useState<ScenarioActiveConversation | null>(null)
  const attackId = active?.attack_result_id ?? saved?.attack_result_id ?? recorded?.attack_result_id ?? lastActive?.attack_result_id ?? null
  const running = Boolean(state.run && !isTerminalRunState(state.run.status))
  const readConversationById = useCallback((selectedId: string) =>
    scenariosApi.getRunConversation(scenarioResultId, selectedId), [scenarioResultId])
  const transcript = useConversationMessages({
    attackResultId: attackId, conversationId, activeConversationId: conversationId,
    sendingConversations: NO_SENDS, isSendInFlight: noSendInFlight,
    readConversationById, pollIntervalMs: running ? SCENARIO_RUN_POLL_INTERVAL_MS : undefined,
  })
  const [cancelOpen, setCancelOpen] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [cancelError, setCancelError] = useState<string | null>(null)
  const verified = transcript.loadedConversationId === conversationId && !transcript.conversationLoadIssue
  if (active && verified && lastActive !== active) setLastActive(active)
  const display = active ?? recorded ?? lastActive
  const stopped = !active && !saved && (recorded !== undefined || (lastActive !== null && state.run?.status === 'CANCELLED'))
  useEffect(() => {
    if (!active && saved && verified) {
      navigate(attackConversationRoutePath(saved.attack_result_id, conversationId, scenarioResultId), { replace: true })
    }
  }, [active, saved, verified, navigate, conversationId, scenarioResultId])

  async function cancel(): Promise<void> {
    setCancelling(true)
    setCancelError(null)
    try {
      applyRunSummary(await scenariosApi.cancelRun(scenarioResultId))
      setCancelOpen(false)
    } catch (error: unknown) {
      setCancelError(toApiError(error).detail)
    } finally {
      setCancelling(false)
    }
  }

  return (
    <section className={styles.root} aria-label="Scan conversation">
      <div className={styles.content}>
        <Link className={styles.backLink} to={scenarioRunRoutePath(scenarioResultId)}><ArrowLeftRegular /> Back to scan</Link>
        <header className={styles.header}>
          <div className={styles.headerIdentity}>
            <div className={styles.titleRow}><Text as="h1" size={600} weight="semibold">{display?.atomic_attack_name ?? saved?.atomic_attack_name ?? 'Conversation'}</Text>
              <Badge appearance="tint" color={active ? 'brand' : 'informative'}>{stopped ? 'Stopped before finishing' : active ? 'Executing' : 'Saved conversation'}</Badge>
            </div>
            <Text className={styles.sectionHint}>Read-only · {state.run?.scenario_registry_name ?? state.run?.scenario_name}</Text>
          </div>
          {running && active && verified && <Button icon={<StopRegular />} className={styles.touchTarget} onClick={() => { setCancelOpen(true) }}>Cancel scan</Button>}
        </header>
        <Text>{display?.objective ?? state.plan?.seed_groups.find((seed) => seed.id === saved?.seed_group_id)?.objective}</Text>
        {stopped && <Text role="status">Saved messages remain available.</Text>}
        {state.error && <MessageBar intent="error"><MessageBarBody>{state.error} <Button onClick={retry}>Retry scan</Button></MessageBarBody></MessageBar>}
        {transcript.conversationLoadIssue && <MessageBar intent="error"><MessageBarBody>
          {transcript.conversationLoadIssue.detail} <Button onClick={() => { void transcript.loadConversation(attackId, conversationId) }}>Retry messages</Button>
        </MessageBarBody></MessageBar>}
        {!verified && !transcript.conversationLoadIssue
          && (state.loadStatus === 'loading' || transcript.isLoadingMessages) && <Spinner label="Loading conversation..." />}
        <div className={styles.conversationSurface} role="log" aria-label="Conversation messages" aria-live="polite">
          <MessageList messages={transcript.messages}
            isLoading={transcript.loadedConversationId === null && (Boolean(attackId) || transcript.isLoadingMessages)} />
        </div>
        {active && <Text size={200} className={styles.sectionHint} role="status">Saved messages update while the attack runs.</Text>}
      </div>
      <Dialog open={cancelOpen} onOpenChange={(_, data) => { if (!cancelling) setCancelOpen(data.open) }}>
        <DialogSurface><DialogBody><DialogTitle>Cancel scan?</DialogTitle><DialogContent>
          Stop remaining attacks. Saved conversations stay available.
          {cancelError && <MessageBar intent="error"><MessageBarBody>{cancelError}</MessageBarBody></MessageBar>}
        </DialogContent><DialogActions>
          <Button disabled={cancelling} onClick={() => { setCancelOpen(false) }}>Keep running</Button>
          <Button appearance="primary" disabled={cancelling} onClick={() => { void cancel() }}>Cancel scan</Button>
        </DialogActions></DialogBody></DialogSurface>
      </Dialog>
    </section>
  )
}
