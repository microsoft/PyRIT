import { Button, Text } from '@fluentui/react-components'
import { useAttackNotFoundStyles } from './AttackNotFound.styles'

interface AttackNotFoundProps {
  attackId: string
  conversationId?: string | null
  onStartNew: () => void
  onBackToHistory: () => void
  onViewAttack?: () => void
  /**
   * 'not-found' for a genuine attack 404;
   * 'error' for a transient attack load failure;
   * 'evidence-missing' when the parent attack exists but the requested evidence conversation is unavailable.
   */
  variant?: 'not-found' | 'error' | 'evidence-missing'
  findingEvidenceId?: string | null
}

export default function AttackNotFound({
  attackId,
  conversationId,
  onStartNew,
  onBackToHistory,
  onViewAttack,
  variant = 'not-found',
  findingEvidenceId,
}: AttackNotFoundProps) {
  const styles = useAttackNotFoundStyles()
  const isError = variant === 'error'
  const isEvidenceConversationUnavailable = variant === 'evidence-missing' || Boolean(findingEvidenceId && conversationId)

  return (
    <div
      className={styles.root}
      data-testid={isError ? 'attack-load-error' : isEvidenceConversationUnavailable ? 'evidence-conversation-missing' : 'attack-not-found'}
    >
      <Text size={500} weight="semibold">
        {isError ? 'Could not load attack' : findingEvidenceId ? 'Evidence unavailable' : 'Attack not found'}
      </Text>
      <Text className={styles.detail}>
        {isError ? (
          <>
            Something went wrong loading the attack{' '}
            <span className={styles.code}>{attackId}</span>. This is usually a temporary network or
            server error — refreshing the page may resolve it.
          </>
        ) : isEvidenceConversationUnavailable ? (
          <>
            The requested evidence conversation{conversationId ? (
              <> <span className={styles.code}>{conversationId}</span></>
            ) : null} is unavailable for attack{' '}
            <span className={styles.code}>{attackId}</span>. It may have been deleted, or the link may be incorrect.
          </>
        ) : (
          <>
            No attack matches the id <span className={styles.code}>{attackId}</span>. It may have been
            deleted, or the link may be incorrect.
          </>
        )}
      </Text>
      <div className={styles.actions}>
        {isEvidenceConversationUnavailable && onViewAttack ? (
          <>
            <Button appearance="primary" onClick={onViewAttack}>
              View parent attack
            </Button>
            <Button appearance="secondary" onClick={onBackToHistory}>
              Back to history
            </Button>
          </>
        ) : (
          <>
            <Button appearance="primary" onClick={onStartNew}>
              Start a new attack
            </Button>
            <Button appearance="secondary" onClick={onBackToHistory}>
              Back to history
            </Button>
          </>
        )}
      </div>
    </div>
  )
}
