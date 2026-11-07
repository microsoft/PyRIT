import { useMemo } from 'react'

import { Button, Text } from '@fluentui/react-components'
import { Link } from 'react-router'

import type { ScenarioActiveConversation, ScenarioProgressResult } from '@/types'
import { scenarioConversationRoutePath } from '@/utils/routeParams'

import { useScenarioRunPageStyles } from './ScenarioRunPage.styles'

interface ScenarioRunConversationsProps {
  readonly scenarioResultId: string
  readonly results: readonly ScenarioProgressResult[]
  readonly activeConversations: readonly ScenarioActiveConversation[]
  readonly recordedConversations: readonly ScenarioActiveConversation[]
  readonly onOpenAttempt: (conversation: ScenarioActiveConversation, trigger: HTMLElement) => void
}

/** Conversations still executing, and interrupted ones saved without a completed result. */
export default function ScenarioRunConversations({
  scenarioResultId, results, activeConversations, recordedConversations, onOpenAttempt,
}: ScenarioRunConversationsProps) {
  const styles = useScenarioRunPageStyles()
  const savedConversations = useMemo(() => {
    const completedConversationIds = new Set(results.flatMap((attempt) => [
      attempt.conversation_id,
      ...(attempt.related_conversation_ids ?? []),
    ]))
    const recorded = new Map<string, ScenarioActiveConversation>()
    for (const conversation of recordedConversations) {
      if (!completedConversationIds.has(conversation.conversation_id)) {
        recorded.set(`${conversation.attack_result_id}:${conversation.conversation_id}`, conversation)
      }
    }
    return [...recorded.values()]
  }, [results, recordedConversations])

  return (
    <>
      {activeConversations.length > 0 && <section className={styles.section} aria-labelledby="live-conversations-heading">
        <Text as="h2" id="live-conversations-heading" size={500} weight="semibold">Executing</Text>
        {activeConversations.map((conversation) => (
          <div key={conversation.conversation_id} className={styles.liveConversation}>
            <Button appearance="transparent" onClick={(event) => { onOpenAttempt(conversation, event.currentTarget) }}>
              {conversation.atomic_attack_name}
            </Button>
            <Text className={styles.preview}>{conversation.objective}</Text>
            <Link to={scenarioConversationRoutePath(scenarioResultId, conversation.conversation_id)}>Watch live conversation</Link>
          </div>
        ))}
      </section>}
      {savedConversations.length > 0 && <section className={styles.section} aria-label="Saved conversations">
        <Text as="h2" size={500} weight="semibold">Saved conversations</Text>
        {savedConversations.map((conversation) => (
          <div key={`${conversation.attack_result_id}:${conversation.conversation_id}`} className={styles.liveConversation}>
            <Text>{conversation.atomic_attack_name}</Text>
            <Text className={styles.preview}>{conversation.objective}</Text>
            <Link to={scenarioConversationRoutePath(scenarioResultId, conversation.conversation_id)}>Open saved conversation</Link>
          </div>
        ))}
      </section>}
    </>
  )
}
