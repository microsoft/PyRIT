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
  // PR6.1 fix: queueDepth must persist across active-wave swaps. The
  // runner emits `queued` only on enqueue and `complete` on dequeue
  // (cancelQueued path) — so the live queue depth is the size of
  // "queued waves that haven't started or been cancelled yet."
  const pendingWaveIds = new Set<string>()
  for (const ev of events) {
    switch (ev.kind) {
      case 'start': {
        // A start pops this wave from the queue (if it was queued).
        pendingWaveIds.delete(ev.waveId)
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
        } else {
          // A complete for a non-active wave is cancelQueued's wire —
          // the wave was in the queue and got dropped without starting.
          pendingWaveIds.delete(ev.waveId)
        }
        break
      }
      case 'queued': {
        pendingWaveIds.add(ev.waveId)
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
  if (active !== null) active.queueDepth = pendingWaveIds.size
  return active ?? { status: 'idle' }
}

/**
 * Append a WaveEvent to the host's buffer, compacting to `[]` once the
 * stream drains to idle (no active wave). Without this the buffer grows
 * unbounded across a long session — every wave appends start +
 * node_complete-per-leaf + complete, and the array is rebuilt on each
 * render. Compaction only fires on a `complete` event (the only kind
 * that can transition to idle), so an in-flight wave's `start` is never
 * dropped mid-wave.
 */
export function appendWaveEvent(
  buffer: ReadonlyArray<WaveEvent>,
  event: WaveEvent,
): WaveEvent[] {
  const next = [...buffer, event]
  if (event.kind === 'complete' && summarizeWaveEvents(next).status === 'idle') {
    return []
  }
  return next
}
