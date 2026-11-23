import { Text } from '@fluentui/react-components'
import { ArrowRightRegular, ChatRegular, CheckmarkCircleRegular, SparkleRegular } from '@fluentui/react-icons'

import { useAttackActivityStyles } from './AttackActivity.styles'

interface AttackActivityProps {
  /** Describe loading a scan rather than an attack that is running. */
  readonly loading?: boolean
  readonly label?: string
}

/** Animated prompt → response → evaluation graphic for work that is still underway. */
export default function AttackActivity({ loading = false, label }: AttackActivityProps) {
  const styles = useAttackActivityStyles()
  return (
    <div className={styles.root} role="status">
      <div className={styles.flow} aria-hidden="true">
        <ChatRegular />
        <span className={styles.connection}><ArrowRightRegular /></span>
        <SparkleRegular />
        <span className={styles.connection}><ArrowRightRegular /></span>
        <CheckmarkCircleRegular />
      </div>
      <div className={styles.copy}>
        <Text weight="semibold">{label ?? (loading ? 'Loading scan' : 'Attack in progress')}</Text>
        <Text size={200} className={styles.hint}>
          {loading ? 'Retrieving configuration, conversations and scores' : 'Prompt, target response, evaluation'}
        </Text>
      </div>
    </div>
  )
}
