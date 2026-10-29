import { Link } from 'react-router'

import { useScenarioQueue } from '@/hooks/useScenarioQueue'

import ScenarioQueue from './ScenarioQueue'
import { useScenarioRunPageStyles } from './ScenarioRunPage.styles'

export default function ScenarioQueuePage() {
  const styles = useScenarioRunPageStyles()
  const queue = useScenarioQueue()
  return <section className={styles.root} aria-label="Scan queue"><div className={styles.content}>
    <Link className={styles.backLink} to="/scanner/history">Back to scans</Link>
    <ScenarioQueue snapshot={queue.snapshot} loading={queue.loading} stale={queue.stale} error={queue.error} />
  </div></section>
}
