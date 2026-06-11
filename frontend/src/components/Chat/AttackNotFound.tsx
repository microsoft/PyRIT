import { Button, Text } from '@fluentui/react-components'
import { useAttackNotFoundStyles } from './AttackNotFound.styles'

interface AttackNotFoundProps {
  attackId: string
  onStartNew: () => void
  onBackToHistory: () => void
}

export default function AttackNotFound({ attackId, onStartNew, onBackToHistory }: AttackNotFoundProps) {
  const styles = useAttackNotFoundStyles()

  return (
    <div className={styles.root} data-testid="attack-not-found">
      <Text size={500} weight="semibold">
        Attack not found
      </Text>
      <Text className={styles.detail}>
        No attack matches the id <span className={styles.code}>{attackId}</span>. It may have been
        deleted, or the link may be incorrect.
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
