import { Button, Popover, PopoverSurface, PopoverTrigger, Text } from '@fluentui/react-components'
import { Link } from 'react-router'

import type { ScenarioActiveConversation } from '@/types'
import { attackConversationRoutePath } from '@/utils/routeParams'

import { useScenarioRunPageStyles } from './ScenarioRunPage.styles'
import type { LiveAttack } from './scenarioRunLayout'

const HEADING_ID = 'live-attacks-heading'

interface LiveAttacksPopoverProps {
  readonly scenarioResultId: string
  readonly liveAttacks: readonly LiveAttack[]
  /** Router state carried to a live conversation, so its back link returns here. */
  readonly conversationState: unknown
  readonly onOpenAttack: (conversation: ScenarioActiveConversation, trigger: HTMLElement) => void
}

/** The attacks running right now, each opening its details or its live conversation in Chat. */
export default function LiveAttacksPopover({ scenarioResultId, liveAttacks, conversationState, onOpenAttack }: LiveAttacksPopoverProps) {
  const styles = useScenarioRunPageStyles()
  return (
    <Popover positioning="below-end" withArrow>
      <PopoverTrigger disableButtonEnhancement>
        <Button className={styles.touchTarget} disabled={liveAttacks.length === 0}>
          View live attacks ({liveAttacks.length})
        </Button>
      </PopoverTrigger>
      <PopoverSurface aria-labelledby={HEADING_ID}>
        <section className={styles.liveActivity}>
          <Text as="h2" id={HEADING_ID} size={500} weight="semibold">Live attacks</Text>
          {liveAttacks.map(({ conversation, conversationCount }) => (
            <div key={conversation.attack_result_id} className={styles.liveConversation}>
              <Button appearance="transparent" onClick={(event) => { onOpenAttack(conversation, event.currentTarget) }}>
                {conversation.atomic_attack_name}
              </Button>
              <Text className={styles.preview}>{conversation.objective}</Text>
              {conversationCount > 1 && <Text size={200} className={styles.sectionHint}>{conversationCount} live conversations</Text>}
              <Link to={attackConversationRoutePath(conversation.attack_result_id, conversation.conversation_id, scenarioResultId)}
                state={conversationState}>
                Watch live conversation
              </Link>
            </div>
          ))}
        </section>
      </PopoverSurface>
    </Popover>
  )
}
