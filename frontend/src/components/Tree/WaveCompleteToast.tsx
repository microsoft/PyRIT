// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Wave-completion toast per spec §2.3 / §8.1. Pure presentational —
 * the host decides when to mount / unmount based on the runner's
 * `complete` WaveEvent stream.
 *
 * Renders the 5-bucket summary tail "57 ✓, 3 ⚠, 0 ⏱, 0 ⦾, 0 ✋"
 * + [Retry failed] / [View wave] / [Dismiss] buttons. [Retry failed]
 * disables when no transient failures exist (rate-limited and
 * permanent failures are excluded — rate-limited needs the operator
 * to wait, permanent needs the operator to fix the input).
 *
 * Auto-dismiss via a setTimeout fired from a useEffect; tests inject
 * `autoDismissMs={0}` to disable.
 */

import { useEffect } from 'react'
import { Button } from '@fluentui/react-components'

import { useWaveCompleteToastStyles } from './WaveCompleteToast.styles'

/** Mirror of the runner's WaveEvent { kind: 'complete' }.summary shape. */
export interface WaveSummary {
  succeeded: number
  failed: {
    transient: number
    rate_limited: number
    permanent: number
  }
  blocked: number
  cancelled: number
  reflog_evicted: number
}

export interface WaveCompleteToastProps {
  summary: WaveSummary
  onRetryFailed?: () => void
  onViewWave?: () => void
  onDismiss?: () => void
  /** Default 8000 ms; 0 disables. */
  autoDismissMs?: number
}

const DEFAULT_AUTO_DISMISS_MS = 8000

export function WaveCompleteToast({
  summary,
  onRetryFailed,
  onViewWave,
  onDismiss,
  autoDismissMs = DEFAULT_AUTO_DISMISS_MS,
}: WaveCompleteToastProps) {
  const styles = useWaveCompleteToastStyles()

  useEffect(() => {
    if (onDismiss === undefined || autoDismissMs <= 0) return undefined
    const id = setTimeout(onDismiss, autoDismissMs)
    return () => clearTimeout(id)
    // `summary` is a deliberate dep: a fresh summary reference means the
    // host swapped in a new wave's toast, so the 8-second timer must
    // restart from zero. Without this dep, a memoized onDismiss would
    // inherit the prior wave's timer remainder (PR6.2 fix per PR6 review).
  }, [autoDismissMs, onDismiss, summary])

  const transient = summary.failed.transient
  const retryDisabled = transient === 0

  return (
    <div data-tree-wave-toast className={styles.toast} role="status">
      <span className={styles.headline}>Wave complete:</span>
      <span className={styles.bucket} title="succeeded">
        {summary.succeeded} ✓
      </span>
      <span className={styles.bucket} title="failed (retryable)">
        {transient} ⚠
      </span>
      <span className={styles.bucket} title="rate-limited">
        {summary.failed.rate_limited} ⏱
      </span>
      <span className={styles.bucket} title="blocked">
        {summary.blocked} ⦾
      </span>
      <span className={styles.bucket} title="needs fix (permanent failure)">
        {summary.failed.permanent} ✋
      </span>
      {onRetryFailed !== undefined && (
        <Button
          size="small"
          appearance="subtle"
          disabled={retryDisabled}
          onClick={onRetryFailed}
        >
          Retry failed
        </Button>
      )}
      {onViewWave !== undefined && (
        <Button size="small" appearance="subtle" onClick={onViewWave}>
          View wave
        </Button>
      )}
      {onDismiss !== undefined && (
        <Button size="small" appearance="subtle" onClick={onDismiss}>
          Dismiss
        </Button>
      )}
    </div>
  )
}
