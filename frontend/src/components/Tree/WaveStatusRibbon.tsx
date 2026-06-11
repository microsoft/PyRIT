// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Canvas-top wave-status ribbon (spec §2.3). Pure presentational —
 * consumes the `WaveStatusState` the host computes from the
 * `WaveEvent` stream via `summarizeWaveEvents`.
 *
 * When `state.status === 'idle'`, the ribbon renders an empty wrapper
 * (the in-flight UI is absent but the slot stays in the DOM for
 * layout stability). When running, the ribbon shows:
 *   - a progress bar (completed / total)
 *   - "N / total" numeric counter + per-outcome glyphs (✓, ⚠)
 *   - a [Cancel] button (when `onCancelWave` is wired)
 *   - a "M queued" indicator + [Cancel queued] (when queueDepth > 0)
 */

import { Button, ProgressBar } from '@fluentui/react-components'

import type { WaveStatusState } from './waveStatus'
import { useWaveStatusRibbonStyles } from './waveStatusRibbon.styles'

export interface WaveStatusRibbonProps {
  state: WaveStatusState
  onCancelWave?: () => void
  onCancelQueued?: () => void
}

export function WaveStatusRibbon({
  state,
  onCancelWave,
  onCancelQueued,
}: WaveStatusRibbonProps) {
  const styles = useWaveStatusRibbonStyles()
  if (state.status === 'idle') {
    return <div data-tree-wave-status data-status="idle" className={styles.ribbon} />
  }
  // running
  const progress =
    state.total > 0 ? Math.min(1, state.completed / state.total) : 0
  return (
    <div
      data-tree-wave-status
      data-status="running"
      data-wave-id={state.waveId}
      className={styles.ribbon}
    >
      <div className={styles.progressWrap}>
        <ProgressBar
          value={progress}
          aria-valuenow={state.completed}
          aria-valuemax={state.total}
          aria-label="Wave progress"
        />
      </div>
      <span className={styles.counter}>
        {state.completed} / {state.total}
      </span>
      <span className={styles.outcome} title={`${state.succeeded} succeeded`}>
        {state.succeeded} ✓
      </span>
      <span className={styles.outcome} title={`${state.failed} failed`}>
        {state.failed} ⚠
      </span>
      {onCancelWave !== undefined && (
        <Button size="small" appearance="subtle" onClick={onCancelWave}>
          Cancel
        </Button>
      )}
      {state.queueDepth > 0 && (
        <span data-testid="wave-status-queue" className={styles.queueChip}>
          {state.queueDepth} queued
        </span>
      )}
      {onCancelQueued !== undefined && state.queueDepth > 0 && (
        <Button size="small" appearance="subtle" onClick={onCancelQueued}>
          Cancel queued
        </Button>
      )}
    </div>
  )
}
