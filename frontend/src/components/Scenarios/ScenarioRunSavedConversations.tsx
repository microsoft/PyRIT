import { Text } from '@fluentui/react-components'
import { Link } from 'react-router'

import type { ScenarioActiveConversation } from '@/types'
import { attackConversationRoutePath } from '@/utils/routeParams'

import { useScenarioRunPageStyles } from './ScenarioRunPage.styles'

interface ScenarioRunSavedConversationsProps {
  readonly scenarioResultId: string
  /** Conversations saved by attacks that stopped before saving a result. */
  readonly conversations: readonly ScenarioActiveConversation[]
  /** Router state carried to the conversation, so its back link returns here. */
  readonly conversationState: unknown
}

/** Interrupted attacks' saved conversations, which have no result row to open them from. */
export default function ScenarioRunSavedConversations({
  scenarioResultId, conversations, conversationState,
}: ScenarioRunSavedConversationsProps) {
  const styles = useScenarioRunPageStyles()
  if (conversations.length === 0) return null
  return (
    <section className={styles.section} aria-labelledby="saved-conversations-heading">
      <Text as="h2" id="saved-conversations-heading" size={500} weight="semibold">Saved conversations</Text>
      {conversations.map((conversation) => (
        <div key={`${conversation.attack_result_id}:${conversation.conversation_id}`} className={styles.liveConversation}>
          <Text>{conversation.atomic_attack_name}</Text>
          <Text className={styles.preview}>{conversation.objective}</Text>
          <Link to={attackConversationRoutePath(conversation.attack_result_id, conversation.conversation_id, scenarioResultId)}
            state={conversationState}>
            Open saved conversation
          </Link>
        </div>
      ))}
    </section>
  )
}
