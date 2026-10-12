import { Button, Text } from '@fluentui/react-components'
import { DismissRegular } from '@fluentui/react-icons'

import type { MessageSendConversation, MessageSendStatus } from '@/types'

import { useMultiSendProgressStyles } from './MultiSendProgress.styles'

interface MultiSendProgressProps {
  progress: MessageSendStatus
  needsRefresh: boolean
  onSelectConversation: (conversationId: string) => void
  onRefresh: () => void
  onDismiss: () => void
}

function describeProgress(progress: MessageSendConversation): string {
  if (progress.failure_stage) return `Error (${progress.failure_stage})`
  return {
    queued: 'Queued', preparing: 'Preparing', sending: 'Sending', finalizing: 'Finishing',
    completed: 'Completed', failed: 'Error', interrupted: 'Interrupted',
  }[progress.state]
}

export default function MultiSendProgress({
  progress, needsRefresh, onSelectConversation, onRefresh, onDismiss,
}: MultiSendProgressProps) {
  const styles = useMultiSendProgressStyles()
  const finished = ['completed', 'failed', 'interrupted'].includes(progress.state)
  return (
    <section className={styles.root} aria-label="Repeated send progress">
      <div className={styles.heading}>
        <Text weight="semibold">Repeat send ({progress.count})</Text>
        {finished && (
          <Button
            appearance="subtle" size="small" className={styles.touchTarget} icon={<DismissRegular />}
            aria-label="Dismiss repeat progress" onClick={onDismiss}
          />
        )}
      </div>
      <div className={styles.conversations} aria-live="polite">
        {progress.conversations?.length ? progress.conversations.map((conversation: MessageSendConversation, index: number) => (
          <Button
            key={conversation.conversation_id} appearance="subtle" size="small" className={styles.touchTarget}
            aria-label={`Open conversation ${conversation.conversation_id}`}
            onClick={() => onSelectConversation(conversation.conversation_id)}
          >
            {index + 1}: {describeProgress(conversation)}
          </Button>
        )) : <Text size={200}>{progress.error ?? 'Preparing conversation copies...'}</Text>}
      </div>
      {needsRefresh && (
        <div className={styles.heading}>
          <Text size={200}>Progress or saved messages could not be loaded. Refresh only; do not resend.</Text>
          <Button size="small" className={styles.touchTarget} onClick={onRefresh}>Refresh progress</Button>
        </div>
      )}
    </section>
  )
}
