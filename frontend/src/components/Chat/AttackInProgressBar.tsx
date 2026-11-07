import { Link } from 'react-router'
import { ArrowLeftRegular } from '@fluentui/react-icons'

import AttackActivity from '@/components/Scenarios/AttackActivity'

import { useAttackInProgressBarStyles } from './AttackInProgressBar.styles'

interface AttackInProgressBarProps {
  /** The scan running the attack, when known. */
  readonly scanPath: string | null
  /** Router state carried back to the scan, so its own back link still works. */
  readonly scanState?: unknown
}

/** Takes the prompt bar's place while a scan is still running the attack shown in Chat. */
export default function AttackInProgressBar({ scanPath, scanState }: AttackInProgressBarProps) {
  const styles = useAttackInProgressBarStyles()
  return (
    <div className={styles.root} data-testid="attack-in-progress-bar">
      <AttackActivity label="Attack in progress" />
      {scanPath && <Link className={styles.scanLink} to={scanPath} state={scanState}>
        <ArrowLeftRegular aria-hidden="true" /> Back to scan
      </Link>}
    </div>
  )
}
