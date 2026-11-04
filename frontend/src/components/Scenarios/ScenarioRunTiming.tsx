import { useEffect, useState } from 'react'

import { Text } from '@fluentui/react-components'

import { formatDuration } from '@/components/AttackResults/attackAttemptFormatting'
import type { ScenarioProgressCounts, ScenarioProgressHeader } from '@/types'
import { getElapsedMilliseconds, getEtaMilliseconds, isTerminalRunState } from '@/utils/scenarioRunProgress'

const CLOCK_INTERVAL_MS = 1_000

interface ScenarioRunTimingProps {
  readonly run: ScenarioProgressHeader
  readonly overall?: ScenarioProgressCounts
}

export default function ScenarioRunTiming({ run, overall }: ScenarioRunTimingProps) {
  const [now, setNow] = useState(Date.now)
  const active = !isTerminalRunState(run.status)
  useEffect(() => {
    if (!active || !run.started_at) return
    const timer = setInterval(() => { setNow(Date.now()) }, CLOCK_INTERVAL_MS)
    return () => { clearInterval(timer) }
  }, [active, run.started_at])
  if (overall) {
    const eta = getEtaMilliseconds(run, overall, now)
    return eta === null ? null : <Text>About {formatDuration(eta)} left</Text>
  }
  const elapsed = run.started_at ? formatDuration(getElapsedMilliseconds(run, now))
    : run.status === 'CREATED' || run.status === 'QUEUED' ? 'Not started' : 'Not recorded'
  return <Text weight="semibold">{elapsed}{active && run.started_at ? ' elapsed' : ''}</Text>
}
