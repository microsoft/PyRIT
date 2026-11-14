import { Button, MessageBar, MessageBarBody } from '@fluentui/react-components'
import { ArrowLeftRegular } from '@fluentui/react-icons'
import { Link, Navigate, useLocation, useParams } from 'react-router'

import { useScenarioRunProgress } from '@/hooks/useScenarioRunProgress'
import { attackConversationRoutePath, routerPathParamValue, scenarioRunRoutePath } from '@/utils/routeParams'
import { isTerminalRunState } from '@/utils/scenarioRunProgress'

import AttackActivity from './AttackActivity'
import { useScenarioRunPageStyles } from './ScenarioRunPage.styles'
import { attackIdForConversation } from './scenarioRunLayout'

/** Opens an old scan conversation link in Chat, which shows running and finished scan attacks alike. */
export default function ScanConversationRedirect() {
  const params = useParams<{ scenarioResultId: string; conversationId: string }>()
  const scenarioResultId = routerPathParamValue(params.scenarioResultId)
  const conversationId = routerPathParamValue(params.conversationId)
  return <ScanConversationLookup key={`${scenarioResultId}:${conversationId}`}
    scenarioResultId={scenarioResultId} conversationId={conversationId} />
}

interface ScanConversationLookupProps {
  readonly scenarioResultId: string
  readonly conversationId: string
}

function ScanConversationLookup({ scenarioResultId, conversationId }: ScanConversationLookupProps) {
  const styles = useScenarioRunPageStyles()
  const location = useLocation()
  const { state, retry } = useScenarioRunProgress(scenarioResultId)
  const attackId = attackIdForConversation(state, conversationId)
  if (attackId) {
    return <Navigate replace state={location.state}
      to={attackConversationRoutePath(attackId, conversationId, scenarioResultId)} />
  }
  const missing = state.run !== null && isTerminalRunState(state.run.status)
  return (
    <main className={styles.root}><div className={styles.content}>
      <Link to={scenarioRunRoutePath(scenarioResultId)} className={styles.backLink}><ArrowLeftRegular /> Back to scan</Link>
      {state.loadStatus === 'loading' || (!missing && !state.error) ? <AttackActivity loading /> : (
        <MessageBar intent="error"><MessageBarBody>
          {missing ? 'This conversation is not part of the scan.' : state.error}
          {!missing && <Button onClick={retry}>Retry</Button>}
        </MessageBarBody></MessageBar>
      )}
    </div></main>
  )
}
