// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Tests for `summarizeWaveEvents` — the pure event-stream reducer that
 * collapses the runner's WaveEvent emissions into the current canvas-
 * top ribbon state per spec §2.3.
 *
 * The reducer is the only piece of UI state needed for the wave-status
 * ribbon's in-flight progress bar; the host appends events as they
 * arrive, the ribbon reads `summarizeWaveEvents(events)` on each render.
 */

import { summarizeWaveEvents } from './waveStatus'
import { treeId, nodeId } from '../../runner/testHelpers'
import type { WaveEvent } from '../../runner/treeTypes'

// ============================================================================
// Helpers — terse WaveEvent factories scoped to test brevity
// ============================================================================

const T = treeId('t-1')
const N = (s: string) => nodeId(s)
const ISO = '2026-06-11T00:00:00.000Z'

function evStart(opts: { waveId: string; estimatedCalls: number }): WaveEvent {
  return {
    kind: 'start',
    waveId: opts.waveId,
    triggerKind: 'refresh_tree',
    estimatedCalls: opts.estimatedCalls,
    treeId: T,
    emittedAt: ISO,
  }
}

function evNodeComplete(opts: { waveId: string; outcome: 'success' | 'failure' }): WaveEvent {
  return {
    kind: 'node_complete',
    waveId: opts.waveId,
    nodeId: N('n'),
    outcome: opts.outcome,
    emittedAt: ISO,
  }
}

function evComplete(opts: {
  waveId: string
  succeeded?: number
  failedTransient?: number
  failedRateLimited?: number
  failedPermanent?: number
  blocked?: number
  cancelled?: number
}): WaveEvent {
  return {
    kind: 'complete',
    waveId: opts.waveId,
    emittedAt: ISO,
    summary: {
      succeeded: opts.succeeded ?? 0,
      failed: {
        transient: opts.failedTransient ?? 0,
        rate_limited: opts.failedRateLimited ?? 0,
        permanent: opts.failedPermanent ?? 0,
      },
      blocked: opts.blocked ?? 0,
      cancelled: opts.cancelled ?? 0,
      reflog_evicted: 0,
    },
  }
}

function evQueued(opts: { waveId: string; queueDepth: number }): WaveEvent {
  return {
    kind: 'queued',
    waveId: opts.waveId,
    treeId: T,
    queueDepth: opts.queueDepth,
    emittedAt: ISO,
  }
}

// ============================================================================
// Idle (no events / all complete)
// ============================================================================

describe('summarizeWaveEvents — idle status', () => {
  it('returns idle when given no events', () => {
    expect(summarizeWaveEvents([])).toEqual({ status: 'idle' })
  })

  it('returns idle after the active wave completed (no queued waves)', () => {
    const events: WaveEvent[] = [
      evStart({ waveId: 'w1', estimatedCalls: 5 }),
      evComplete({ waveId: 'w1', succeeded: 5 }),
    ]
    expect(summarizeWaveEvents(events)).toEqual({ status: 'idle' })
  })
})

// ============================================================================
// Running — single wave in flight
// ============================================================================

describe('summarizeWaveEvents — running status', () => {
  it('start with no node_completes → completed=0, total=estimatedCalls', () => {
    const result = summarizeWaveEvents([evStart({ waveId: 'w1', estimatedCalls: 60 })])
    expect(result).toEqual({
      status: 'running',
      waveId: 'w1',
      total: 60,
      completed: 0,
      succeeded: 0,
      failed: 0,
      queueDepth: 0,
    })
  })

  it('counts node_complete outcomes in succeeded / failed', () => {
    const result = summarizeWaveEvents([
      evStart({ waveId: 'w1', estimatedCalls: 60 }),
      evNodeComplete({ waveId: 'w1', outcome: 'success' }),
      evNodeComplete({ waveId: 'w1', outcome: 'success' }),
      evNodeComplete({ waveId: 'w1', outcome: 'failure' }),
    ])
    expect(result).toEqual({
      status: 'running',
      waveId: 'w1',
      total: 60,
      completed: 3,
      succeeded: 2,
      failed: 1,
      queueDepth: 0,
    })
  })

  it('node_completes for a different waveId do NOT count', () => {
    const result = summarizeWaveEvents([
      evStart({ waveId: 'w1', estimatedCalls: 60 }),
      evNodeComplete({ waveId: 'older-wave', outcome: 'success' }),
    ])
    expect(result).toMatchObject({
      status: 'running',
      waveId: 'w1',
      completed: 0,
    })
  })

  it('successful wave start after a previous wave completed → new wave is active', () => {
    const result = summarizeWaveEvents([
      evStart({ waveId: 'w1', estimatedCalls: 5 }),
      evComplete({ waveId: 'w1', succeeded: 5 }),
      evStart({ waveId: 'w2', estimatedCalls: 10 }),
      evNodeComplete({ waveId: 'w2', outcome: 'success' }),
    ])
    expect(result).toMatchObject({
      status: 'running',
      waveId: 'w2',
      total: 10,
      completed: 1,
      succeeded: 1,
    })
  })
})

// ============================================================================
// Queued — waiting waves
// ============================================================================

describe('summarizeWaveEvents — queued behind a running wave', () => {
  it("reports queueDepth from the most recent 'queued' event for a future wave", () => {
    const result = summarizeWaveEvents([
      evStart({ waveId: 'w1', estimatedCalls: 5 }),
      evQueued({ waveId: 'w2', queueDepth: 1 }),
      evQueued({ waveId: 'w3', queueDepth: 2 }),
    ])
    expect(result).toMatchObject({
      status: 'running',
      waveId: 'w1',
      queueDepth: 2,
    })
  })

  it('queueDepth resets to 0 after the active wave completes (queue is consumed by next start)', () => {
    const result = summarizeWaveEvents([
      evStart({ waveId: 'w1', estimatedCalls: 5 }),
      evQueued({ waveId: 'w2', queueDepth: 1 }),
      evComplete({ waveId: 'w1', succeeded: 5 }),
      evStart({ waveId: 'w2', estimatedCalls: 8 }),
    ])
    expect(result).toEqual({
      status: 'running',
      waveId: 'w2',
      total: 8,
      completed: 0,
      succeeded: 0,
      failed: 0,
      queueDepth: 0,
    })
  })

  it('queueDepth carries the REMAINING queued waves across active-wave swap (PR6.1 fix)', () => {
    // Spec §2.3 [Cancel queued] surface fires "when the per-tree queue is
    // non-empty". The reducer used to derive queueDepth from the most
    // recent `queued` event, which reset to 0 every time a new wave
    // started — even when the runner's queue still held entries.
    //
    // Walk: w1 active, w2/w3/w4 queued. w1 completes; w2 pops to active.
    // Ribbon should show "2 queued" (w3 + w4 still pending), not "0".
    const result = summarizeWaveEvents([
      evStart({ waveId: 'w1', estimatedCalls: 5 }),
      evQueued({ waveId: 'w2', queueDepth: 1 }),
      evQueued({ waveId: 'w3', queueDepth: 2 }),
      evQueued({ waveId: 'w4', queueDepth: 3 }),
      evComplete({ waveId: 'w1', succeeded: 5 }),
      evStart({ waveId: 'w2', estimatedCalls: 8 }),
    ])
    expect(result).toMatchObject({
      status: 'running',
      waveId: 'w2',
      queueDepth: 2, // w3 + w4 still in the runner's queue
    })
  })

  it('cancelling a queued wave drops it from the depth count', () => {
    // cancelQueued emits a `complete` event for the cancelled wave id
    // with cancelled=leafCount. The reducer should treat that as "this
    // wave never popped to active" → remove from pending set.
    const result = summarizeWaveEvents([
      evStart({ waveId: 'w1', estimatedCalls: 5 }),
      evQueued({ waveId: 'w2', queueDepth: 1 }),
      evQueued({ waveId: 'w3', queueDepth: 2 }),
      evComplete({ waveId: 'w3', cancelled: 5 }), // cancelQueued for w3
    ])
    expect(result).toMatchObject({
      status: 'running',
      waveId: 'w1',
      queueDepth: 1, // only w2 left
    })
  })
})

// ============================================================================
// Unknown events are tolerated (forward-compat)
// ============================================================================

describe('summarizeWaveEvents — forward-compat', () => {
  it('ignores busy / reflog_eviction / operator_tag_required (no impact on progress)', () => {
    const events: WaveEvent[] = [
      evStart({ waveId: 'w1', estimatedCalls: 3 }),
      { kind: 'busy', treeId: T, holderTabId: 'other', emittedAt: ISO },
      {
        kind: 'reflog_eviction',
        treeId: T,
        nodeId: N('n'),
        evictedExecutionId: 'e1',
        preview: 'x',
        emittedAt: ISO,
      },
      { kind: 'operator_tag_required', treeId: T, emittedAt: ISO },
      evNodeComplete({ waveId: 'w1', outcome: 'success' }),
    ]
    expect(summarizeWaveEvents(events)).toMatchObject({
      status: 'running',
      waveId: 'w1',
      total: 3,
      completed: 1,
      succeeded: 1,
    })
  })
})

// ============================================================================
// Pre-start events — reducer must tolerate node_complete/queued before start
// ============================================================================

describe('summarizeWaveEvents — pre-start events', () => {
  it('node_complete before any start event is dropped (active === null)', () => {
    // Defensive: the runner shouldn't emit node_complete without a
    // preceding start, but if a stale event arrives the reducer must
    // ignore it cleanly rather than crash on the active.completed += 1
    // path.
    const events: WaveEvent[] = [
      evNodeComplete({ waveId: 'orphan', outcome: 'success' }),
    ]
    expect(summarizeWaveEvents(events)).toEqual({ status: 'idle' })
  })

  it('queued before any start event still tracks queueDepth for a later start', () => {
    // queued events can arrive before the first start: the shim queues
    // wave-2 while wave-1's start event is still racing through the
    // host's event buffer. After w1 starts, queueDepth must reflect w2.
    const events: WaveEvent[] = [
      evQueued({ waveId: 'w2', queueDepth: 1 }),
      evStart({ waveId: 'w1', estimatedCalls: 5 }),
    ]
    expect(summarizeWaveEvents(events)).toMatchObject({
      status: 'running',
      waveId: 'w1',
      queueDepth: 1,
    })
  })

  it('queued event for the same wave that later starts does NOT inflate queueDepth', () => {
    // The reducer pops the wave from pendingWaveIds on its start event
    // (line: pendingWaveIds.delete(ev.waveId)). Verify that a wave can
    // be queued then started cleanly with queueDepth=0.
    const events: WaveEvent[] = [
      evQueued({ waveId: 'w1', queueDepth: 1 }),
      evStart({ waveId: 'w1', estimatedCalls: 5 }),
    ]
    expect(summarizeWaveEvents(events)).toMatchObject({
      status: 'running',
      waveId: 'w1',
      queueDepth: 0,
    })
  })
})
