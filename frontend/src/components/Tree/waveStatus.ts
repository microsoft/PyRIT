// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Pure event-stream reducer for the wave-status ribbon (spec §2.3).
 *
 * Walks the host-collected WaveEvent array and returns the current
 * ribbon state. Designed to be cheap (single linear pass) so the
 * caller can re-run on every render without memoizing.
 *
 * V1.0 surfaces:
 *   - `idle` — no active wave; ribbon hides its in-flight UI
 *   - `running` — active wave; ribbon shows progress + cancel chip
 *
 * Queue depth is reported on the active running state so the ribbon
 * can show "1 wave queued" alongside the progress bar.
 */

import type { WaveEvent } from '../../runner/treeTypes'

export type WaveStatusState =
  | { status: 'idle' }
  | {
      status: 'running'
      waveId: string
      /** Estimated total target calls for the active wave (per §8.1 modal). */
      total: number
      /** Sum of `succeeded + failed` for the active wave so far. */
      completed: number
      succeeded: number
      failed: number
      /** Most recently observed queue depth for this wave's queue. */
      queueDepth: number
    }

export function summarizeWaveEvents(events: ReadonlyArray<WaveEvent>): WaveStatusState {
  let active: Extract<WaveStatusState, { status: 'running' }> | null = null
  for (const ev of events) {
    switch (ev.kind) {
      case 'start': {
        // A new start always supersedes any previous active wave; the
        // PR4e shim emits 'complete' before the queued wave's 'start',
        // but the reducer doesn't depend on that invariant.
        active = {
          status: 'running',
          waveId: ev.waveId,
          total: ev.estimatedCalls,
          completed: 0,
          succeeded: 0,
          failed: 0,
          queueDepth: 0,
        }
        break
      }
      case 'node_complete': {
        if (active === null || ev.waveId !== active.waveId) break
        active.completed += 1
        if (ev.outcome === 'success') active.succeeded += 1
        else active.failed += 1
        break
      }
      case 'complete': {
        if (active !== null && ev.waveId === active.waveId) {
          active = null
        }
        break
      }
      case 'queued': {
        if (active !== null) {
          active.queueDepth = ev.queueDepth
        }
        break
      }
      case 'busy':
      case 'reflog_eviction':
      case 'operator_tag_required':
        // Ribbon doesn't track these; PR6d toast + PR6e drawer + the
        // operator-tag-required modal own their own surfaces.
        break
    }
  }
  return active ?? { status: 'idle' }
}
