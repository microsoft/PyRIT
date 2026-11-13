import type { Ref } from 'react'

import { Button, MessageBar, MessageBarBody, Text } from '@fluentui/react-components'
import { ArrowClockwiseRegular, PlayRegular, StopRegular } from '@fluentui/react-icons'
import { Link } from 'react-router'

import ScanStatusBadge from '@/components/ScanStatusBadge'
import type { ScenarioRunControls } from '@/hooks/useScenarioRunControls'
import type { ScenarioProgressHeader } from '@/types'

import { useScenarioRunPageStyles } from './ScenarioRunPage.styles'

interface ScenarioRunHeaderProps {
  readonly run: ScenarioProgressHeader
  readonly active: boolean
  readonly controls: ScenarioRunControls
  /** Focus target for returning to the run overview. */
  readonly headerRef: Ref<HTMLElement>
}

/** The scan's title, state, and run controls, with any control failure below them. */
export default function ScenarioRunHeader({ run, active, controls, headerRef }: ScenarioRunHeaderProps) {
  const styles = useScenarioRunPageStyles()
  const resumable = run.status === 'FAILED' || run.status === 'CANCELLED'
  return (
    <>
      <header className={styles.header} ref={headerRef} tabIndex={-1}>
        <div className={styles.headerIdentity}>
          <div className={styles.titleRow}>
            <Text as="h1" size={700} weight="semibold">{run.scenario_registry_name ?? run.scenario_name}</Text>
            <ScanStatusBadge status={run.status} testId="run-state-badge" />
          </div>
          {run.description && <Text className={styles.sectionHint}>{run.description}</Text>}
        </div>
        <div className={styles.headerActions}>
          {active && <Link className={styles.backLink} to="/scanner/queue">View queue</Link>}
          <Button icon={<ArrowClockwiseRegular />} disabled={controls.rerunPending} onClick={controls.rerun}>
            {controls.rerunPending ? 'Loading...' : 'Rerun'}
          </Button>
          {active ? (
            <Button icon={<StopRegular />} className={styles.touchTarget} onClick={() => { controls.setCancelOpen(true) }}>
              Cancel
            </Button>
          ) : resumable && (
            <Button appearance="primary" icon={<PlayRegular />} disabled={controls.resumePending} onClick={controls.resume}>
              Resume
            </Button>
          )}
        </div>
      </header>
      {controls.controlError && <MessageBar intent="error"><MessageBarBody>
        {controls.controlError}
      </MessageBarBody></MessageBar>}
    </>
  )
}
