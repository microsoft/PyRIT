import { Button, Text } from '@fluentui/react-components'
import { useAttackNotFoundStyles } from './AttackNotFound.styles'

interface AttackNotFoundProps {
  attackId: string
  onStartNew: () => void
  onBackToHistory: () => void
  /** 'not-found' for a genuine 404; 'error' for a transient load failure. */
  variant?: 'not-found' | 'error'
}

export default function AttackNotFound({
  attackId,
  onStartNew,
  onBackToHistory,
  variant = 'not-found',
}: AttackNotFoundProps) {
  const styles = useAttackNotFoundStyles()
  const isError = variant === 'error'

  return (
    <div className={styles.root} data-testid={isError ? 'attack-load-error' : 'attack-not-found'}>
      <Text size={500} weight="semibold">
        {isError ? 'Could not load attack' : 'Attack not found'}
      </Text>
      <Text className={styles.detail}>
        {isError ? (
          <>
            Something went wrong loading the attack{' '}
            <span className={styles.code}>{attackId}</span>. This is usually a temporary network or
            server error — refreshing the page may resolve it.
          </>
        ) : (
          <>
            No attack matches the id <span className={styles.code}>{attackId}</span>. It may have been
            deleted, or the link may be incorrect.
          </>
        )}
      </Text>
      <div className={styles.actions}>
        <Button appearance="primary" onClick={onStartNew}>
          Start a new attack
        </Button>
        <Button appearance="secondary" onClick={onBackToHistory}>
          Back to history
        </Button>
      </div>
    </div>
  )
}
