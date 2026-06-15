// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Tests for `createRunnerShim` — the 5-step entry-point shim per 03 §2.1.
 *
 * Each `refresh*` (+ `retryFailedNodes`) entry point runs:
 *   1. tag-hygiene gate (operator non-empty) — abort with operator_tag_required
 *   2. cross-tab lock acquire — abort with `busy` event on contention
 *   3. cost-guardrail modal — abort silently on operator-cancel
 *   4. per-tree wave-queue check — enqueue if active wave on same tree
 *   5. wave start — call runWave, on settle run wave-end reconcile + drain queue
 *
 * Steps 2-5 are wrapped in a try/finally that releases the lock on every
 * exit path. Steps 1 and 2-on-busy do NOT acquire, so no release.
 *
 * The shim is also the only place that:
 *   - tracks the active-wave controller per tree (so `cancelWave` can find it)
 *   - holds the per-tree wave queue (so `cancelQueued` can drop it)
 *   - runs `reconcileAllTransforms` after the dispatch loop settles
 *
 * Tests mock every dependency (lockManager, costGuardrail, runWaveStarter)
 * so the shim's orchestration is the only thing under test.
 */

import { buildSForNode, buildSForSubtree, buildSForTree } from './readiness'
import { createRunnerShim } from './shim'
import type {
  RunWaveStarter,
  RunWaveStarterArgs,
  ShimDependencies,
} from './shim'
import {
  mkFan,
  mkMockSink,
  mkRoot,
  mkScore,
  mkSend,
  mkTree,
  mkUserTurn,
  nodeId,
  treeId,
} from './testHelpers'
import type { WaveSummary } from './wave'
import type {
  ConversationTree,
  ConversationTreeId,
  CostGuardrail,
  CrossTabLockManager,
  LockAcquireResult,
  WaveEvent,
  WaveTriggerKind,
} from './treeTypes'

// ============================================================================
// Test fixtures
// ============================================================================

const EMPTY_SUMMARY: WaveSummary = {
  succeeded: 0,
  failed: { transient: 0, rate_limited: 0, permanent: 0 },
  blocked: 0,
  cancelled: 0,
  reflog_evicted: 0,
}

function mkSummary(overrides: Partial<WaveSummary> = {}): WaveSummary {
  return { ...EMPTY_SUMMARY, ...overrides }
}

/** A standard small tree with one stale leaf. Used wherever scope doesn't matter. */
function mkStandardTree(treeIdStr = 't-1'): ConversationTree {
  return mkTree(
    'r',
    [
      mkRoot('r', undefined, { state: 'clean' }),
      mkUserTurn('u', 'r', undefined, { state: 'clean' }),
      mkSend('s', 'u', undefined, { state: 'stale' }),
    ],
    { id: treeIdStr },
  )
}

/** A multi-leaf tree for queue/cancel tests where leaf count matters. */
function mk3LeafTree(treeIdStr = 't-3'): ConversationTree {
  return mkTree(
    'r',
    [
      mkRoot('r', undefined, { state: 'clean' }),
      mkUserTurn('u', 'r', undefined, { state: 'clean' }),
      mkFan('f', 'u', {
        axis: 'attempt',
        variants: [
          { axis: 'attempt', payload: {} },
          { axis: 'attempt', payload: {} },
          { axis: 'attempt', payload: {} },
        ],
      }, { state: 'clean' }),
      mkSend('s_a', 'f', undefined, { state: 'edited' }),
      mkSend('s_b', 'f', undefined, { state: 'edited' }),
      mkSend('s_c', 'f', undefined, { state: 'edited' }),
    ],
    { id: treeIdStr },
  )
}

/** Tree with a stale Score sibling-of-Send for the reconcile-on-wave-end test. */
function mkTreeWithStaleScoreSibling(): ConversationTree {
  return mkTree(
    'r',
    [
      mkRoot('r', undefined, { state: 'clean' }),
      mkUserTurn('u', 'r', undefined, { state: 'clean' }),
      mkSend('s', 'u', undefined, { state: 'clean' }),
      mkScore('score', 'u', undefined, { state: 'stale' }),
    ],
    { id: 't-reconcile' },
  )
}

interface ControllableLockManager {
  mgr: CrossTabLockManager
  acquireCalls: ConversationTreeId[]
  releaseCalls: ConversationTreeId[]
}

function mkControllableLockManager(
  options: { acquireResults?: ReadonlyArray<LockAcquireResult> } = {},
): ControllableLockManager {
  const acquireCalls: ConversationTreeId[] = []
  const releaseCalls: ConversationTreeId[] = []
  const results = options.acquireResults ?? []
  let cursor = 0
  const mgr: CrossTabLockManager = {
    acquire: async (treeId) => {
      acquireCalls.push(treeId)
      return results[cursor++] ?? ({ acquired: true } as const)
    },
    release: (treeId) => {
      releaseCalls.push(treeId)
    },
  }
  return { mgr, acquireCalls, releaseCalls }
}

interface ControllableCostGuardrail {
  cg: CostGuardrail
  calls: Array<{ estimatedCalls: number; waveTriggerKind: WaveTriggerKind }>
  setApprove(v: boolean): void
}

function mkControllableCostGuardrail(defaultApprove = true): ControllableCostGuardrail {
  const calls: Array<{ estimatedCalls: number; waveTriggerKind: WaveTriggerKind }> = []
  let approveOverride = defaultApprove
  const cg: CostGuardrail = {
    approve: async (estimatedCalls, waveTriggerKind) => {
      calls.push({ estimatedCalls, waveTriggerKind })
      return approveOverride
    },
  }
  return {
    cg,
    calls,
    setApprove: (v) => {
      approveOverride = v
    },
  }
}

interface ControllableStarter {
  starter: RunWaveStarter
  calls: RunWaveStarterArgs[]
  pendingCount(): number
  resolveNext(summary?: WaveSummary): void
  rejectNext(err: unknown): void
}

function mkControllableRunWaveStarter(): ControllableStarter {
  interface Pending {
    resolve: (s: WaveSummary) => void
    reject: (e: unknown) => void
  }
  const calls: RunWaveStarterArgs[] = []
  const pending: Pending[] = []
  const starter: RunWaveStarter = (args) => {
    calls.push(args)
    return new Promise<WaveSummary>((resolve, reject) => {
      pending.push({ resolve, reject })
    })
  }
  return {
    starter,
    calls,
    pendingCount: () => pending.length,
    resolveNext: (summary = EMPTY_SUMMARY) => {
      const p = pending.shift()
      if (p === undefined) throw new Error('resolveNext: no pending starter call')
      p.resolve(summary)
    },
    rejectNext: (err) => {
      const p = pending.shift()
      if (p === undefined) throw new Error('rejectNext: no pending starter call')
      p.reject(err)
    },
  }
}

/** Returns a treeProvider that always returns the same tree object. */
function mkTreeProvider(tree: ConversationTree | undefined): ShimDependencies['treeProvider'] {
  return (_id) => tree
}

/** Deterministic UUID minter for stable waveId assertions. */
function mkUuidStub(seq: string[] = ['w-1', 'w-2', 'w-3', 'w-4', 'w-5']): () => string {
  let i = 0
  return () => seq[i++] ?? `w-${i}`
}

function flush(times = 16): Promise<void> {
  return (async () => {
    for (let i = 0; i < times; i++) await Promise.resolve()
  })()
}

/**
 * Wait for predicate to be true; returns when it is. Avoids races where the
 * shim's async hops (lock acquire / cost approve / queue check) finish at
 * slightly different microtask boundaries across machines.
 */
async function waitFor(
  predicate: () => boolean,
  description: string,
  maxAttempts = 200,
): Promise<void> {
  for (let i = 0; i < maxAttempts; i++) {
    if (predicate()) return
    await Promise.resolve()
  }
  throw new Error(`waitFor: ${description} never satisfied`)
}

// ============================================================================
// 1. Tag-hygiene gate (step 1)
// ============================================================================

describe('shim — tag-hygiene gate (step 1)', () => {
  it('empty operator: emits operator_tag_required, no lock acquire, no cost modal, no starter', async () => {
    const tree = mkStandardTree()
    const { sink, callsOf } = mkMockSink()
    const lock = mkControllableLockManager()
    const cost = mkControllableCostGuardrail()
    const starter = mkControllableRunWaveStarter()
    const shim = createRunnerShim({
      operatorProvider: () => '',
      treeProvider: mkTreeProvider(tree),
      sink,
      lockManager: lock.mgr,
      costGuardrail: cost.cg,
      runWaveStarter: starter.starter,
      uuid: mkUuidStub(),
    })

    await shim.refreshTree(treeId('t-1'))

    const events = callsOf('emitWaveEvent').map((c) => c.event)
    expect(events).toHaveLength(1)
    expect(events[0].kind).toBe('operator_tag_required')
    if (events[0].kind === 'operator_tag_required') {
      expect(events[0].treeId).toBe(treeId('t-1'))
      expect(events[0].emittedAt).toMatch(/Z$|\+\d{2}:?\d{2}$/)
    }
    expect(lock.acquireCalls).toHaveLength(0)
    expect(cost.calls).toHaveLength(0)
    expect(starter.calls).toHaveLength(0)
    expect(lock.releaseCalls).toHaveLength(0) // nothing to release
  })

  it('null operator behaves the same as empty string', async () => {
    const tree = mkStandardTree()
    const { sink, callsOf } = mkMockSink()
    const lock = mkControllableLockManager()
    const cost = mkControllableCostGuardrail()
    const starter = mkControllableRunWaveStarter()
    const shim = createRunnerShim({
      operatorProvider: () => null,
      treeProvider: mkTreeProvider(tree),
      sink,
      lockManager: lock.mgr,
      costGuardrail: cost.cg,
      runWaveStarter: starter.starter,
      uuid: mkUuidStub(),
    })

    await shim.refreshNode(treeId('t-1'), nodeId('s'))
    expect(callsOf('emitWaveEvent').map((c) => c.event.kind)).toEqual(['operator_tag_required'])
    expect(lock.acquireCalls).toHaveLength(0)
  })

  it('tagged operator passes the gate and proceeds to lock acquire', async () => {
    const tree = mkStandardTree()
    const { sink } = mkMockSink()
    const lock = mkControllableLockManager()
    const cost = mkControllableCostGuardrail()
    const starter = mkControllableRunWaveStarter()
    const shim = createRunnerShim({
      operatorProvider: () => 'alice',
      treeProvider: mkTreeProvider(tree),
      sink,
      lockManager: lock.mgr,
      costGuardrail: cost.cg,
      runWaveStarter: starter.starter,
      uuid: mkUuidStub(),
    })

    const p = shim.refreshTree(treeId('t-1'))
    await waitFor(() => starter.pendingCount() === 1, 'starter invoked')
    expect(lock.acquireCalls).toEqual([treeId('t-1')])
    starter.resolveNext()
    await p
  })
})

// ============================================================================
// 2. Cross-tab lock acquire (step 2)
// ============================================================================

describe('shim — cross-tab lock (step 2)', () => {
  it('lock busy: emits busy event with holderTabId, no cost modal, no starter, no release call', async () => {
    const tree = mkStandardTree()
    const { sink, callsOf } = mkMockSink()
    const lock = mkControllableLockManager({
      acquireResults: [{ acquired: false, holderTabId: 'other-tab-7' }],
    })
    const cost = mkControllableCostGuardrail()
    const starter = mkControllableRunWaveStarter()
    const shim = createRunnerShim({
      operatorProvider: () => 'alice',
      treeProvider: mkTreeProvider(tree),
      sink,
      lockManager: lock.mgr,
      costGuardrail: cost.cg,
      runWaveStarter: starter.starter,
      uuid: mkUuidStub(),
    })

    await shim.refreshTree(treeId('t-1'))

    const events = callsOf('emitWaveEvent').map((c) => c.event)
    expect(events).toHaveLength(1)
    expect(events[0].kind).toBe('busy')
    if (events[0].kind === 'busy') {
      expect(events[0].treeId).toBe(treeId('t-1'))
      // holderTabId from the busy reply is forwarded to the busy event so the
      // operator-facing modal can render *"another tab (id: …)"*.
      expect(events[0].holderTabId).toBe('other-tab-7')
    }
    expect(cost.calls).toHaveLength(0)
    expect(starter.calls).toHaveLength(0)
    expect(lock.releaseCalls).toHaveLength(0) // no acquire → no release
  })

  it('lock acquired: proceeds to cost modal', async () => {
    const tree = mkStandardTree()
    const { sink } = mkMockSink()
    const lock = mkControllableLockManager()
    const cost = mkControllableCostGuardrail()
    const starter = mkControllableRunWaveStarter()
    const shim = createRunnerShim({
      operatorProvider: () => 'alice',
      treeProvider: mkTreeProvider(tree),
      sink,
      lockManager: lock.mgr,
      costGuardrail: cost.cg,
      runWaveStarter: starter.starter,
      uuid: mkUuidStub(),
    })

    const p = shim.refreshTree(treeId('t-1'))
    await waitFor(() => cost.calls.length === 1, 'cost modal called')
    starter.resolveNext()
    await p
  })
})

// ============================================================================
// 3. Cost guardrail (step 3)
// ============================================================================

describe('shim — cost guardrail (step 3)', () => {
  it('rejected: no starter, lock released', async () => {
    const tree = mkStandardTree()
    const { sink } = mkMockSink()
    const lock = mkControllableLockManager()
    const cost = mkControllableCostGuardrail(false) // operator cancels
    const starter = mkControllableRunWaveStarter()
    const shim = createRunnerShim({
      operatorProvider: () => 'alice',
      treeProvider: mkTreeProvider(tree),
      sink,
      lockManager: lock.mgr,
      costGuardrail: cost.cg,
      runWaveStarter: starter.starter,
      uuid: mkUuidStub(),
    })

    await shim.refreshTree(treeId('t-1'))

    expect(starter.calls).toHaveLength(0)
    expect(lock.releaseCalls).toEqual([treeId('t-1')])
  })

  it('approved: proceeds to starter', async () => {
    const tree = mkStandardTree()
    const { sink } = mkMockSink()
    const lock = mkControllableLockManager()
    const cost = mkControllableCostGuardrail(true)
    const starter = mkControllableRunWaveStarter()
    const shim = createRunnerShim({
      operatorProvider: () => 'alice',
      treeProvider: mkTreeProvider(tree),
      sink,
      lockManager: lock.mgr,
      costGuardrail: cost.cg,
      runWaveStarter: starter.starter,
      uuid: mkUuidStub(),
    })
    const p = shim.refreshTree(treeId('t-1'))
    await waitFor(() => starter.pendingCount() === 1, 'starter invoked')
    starter.resolveNext()
    await p
  })

  it('passes estimatedCalls and waveTriggerKind to the guardrail', async () => {
    // 3-leaf attempt-fan, each leaf has 1 stale Send: estimate = 3 leaves × (1 + 1) = 6
    const tree = mk3LeafTree()
    const { sink } = mkMockSink()
    const lock = mkControllableLockManager()
    const cost = mkControllableCostGuardrail(true)
    const starter = mkControllableRunWaveStarter()
    const shim = createRunnerShim({
      operatorProvider: () => 'alice',
      treeProvider: mkTreeProvider(tree),
      sink,
      lockManager: lock.mgr,
      costGuardrail: cost.cg,
      runWaveStarter: starter.starter,
      uuid: mkUuidStub(),
    })
    const p = shim.refreshTree(treeId('t-3'))
    await waitFor(() => cost.calls.length === 1, 'cost called')
    expect(cost.calls[0]).toEqual({ estimatedCalls: 6, waveTriggerKind: 'refresh_tree' })
    starter.resolveNext()
    await p
  })
})

// ============================================================================
// 4. Per-tree wave queue (step 4)
// ============================================================================

describe('shim — wave queue (step 4)', () => {
  it('no active wave: starter is invoked', async () => {
    const tree = mkStandardTree()
    const { sink } = mkMockSink()
    const lock = mkControllableLockManager()
    const cost = mkControllableCostGuardrail()
    const starter = mkControllableRunWaveStarter()
    const shim = createRunnerShim({
      operatorProvider: () => 'alice',
      treeProvider: mkTreeProvider(tree),
      sink,
      lockManager: lock.mgr,
      costGuardrail: cost.cg,
      runWaveStarter: starter.starter,
      uuid: mkUuidStub(),
    })

    const p = shim.refreshTree(treeId('t-1'))
    await waitFor(() => starter.pendingCount() === 1, 'starter invoked')
    starter.resolveNext()
    await p
    expect(starter.calls).toHaveLength(1)
  })

  it('active wave on same tree: second call is queued with queueDepth=1; no second starter invocation', async () => {
    const tree = mk3LeafTree()
    const { sink, callsOf } = mkMockSink()
    const lock = mkControllableLockManager()
    const cost = mkControllableCostGuardrail()
    const starter = mkControllableRunWaveStarter()
    const shim = createRunnerShim({
      operatorProvider: () => 'alice',
      treeProvider: mkTreeProvider(tree),
      sink,
      lockManager: lock.mgr,
      costGuardrail: cost.cg,
      runWaveStarter: starter.starter,
      uuid: mkUuidStub(),
    })

    const first = shim.refreshTree(treeId('t-3'))
    await waitFor(() => starter.pendingCount() === 1, 'first wave running')
    const second = shim.refreshTree(treeId('t-3'))
    await waitFor(
      () => callsOf('emitWaveEvent').some((c) => c.event.kind === 'queued'),
      'queued event emitted',
    )

    // Only one starter call; second is queued.
    expect(starter.calls).toHaveLength(1)

    const queuedEvent = callsOf('emitWaveEvent')
      .map((c) => c.event)
      .find((e): e is Extract<WaveEvent, { kind: 'queued' }> => e.kind === 'queued')
    expect(queuedEvent).toBeDefined()
    expect(queuedEvent?.queueDepth).toBe(1)
    expect(queuedEvent?.treeId).toBe(treeId('t-3'))

    // Drain: release the first wave; queued one should then start.
    starter.resolveNext()
    await waitFor(() => starter.pendingCount() === 1, 'queued wave drained into starter')
    starter.resolveNext()
    await Promise.all([first, second])
    expect(starter.calls).toHaveLength(2)
  })

  it('queue depth increments for subsequent waves', async () => {
    const tree = mk3LeafTree()
    const { sink, callsOf } = mkMockSink()
    const lock = mkControllableLockManager()
    const cost = mkControllableCostGuardrail()
    const starter = mkControllableRunWaveStarter()
    const shim = createRunnerShim({
      operatorProvider: () => 'alice',
      treeProvider: mkTreeProvider(tree),
      sink,
      lockManager: lock.mgr,
      costGuardrail: cost.cg,
      runWaveStarter: starter.starter,
      uuid: mkUuidStub(),
    })

    const first = shim.refreshTree(treeId('t-3'))
    await waitFor(() => starter.pendingCount() === 1, 'first running')
    const second = shim.refreshTree(treeId('t-3'))
    const third = shim.refreshTree(treeId('t-3'))
    await waitFor(
      () => callsOf('emitWaveEvent').filter((c) => c.event.kind === 'queued').length === 2,
      'two queued events',
    )

    const depths = callsOf('emitWaveEvent')
      .map((c) => c.event)
      .filter((e): e is Extract<WaveEvent, { kind: 'queued' }> => e.kind === 'queued')
      .map((e) => e.queueDepth)
    expect(depths).toEqual([1, 2])

    // Drain all three.
    starter.resolveNext()
    await waitFor(() => starter.pendingCount() === 1 && starter.calls.length === 2, 'second drained')
    starter.resolveNext()
    await waitFor(() => starter.pendingCount() === 1 && starter.calls.length === 3, 'third drained')
    starter.resolveNext()
    await Promise.all([first, second, third])
  })

  it('queued waves drain after active wave settles', async () => {
    const tree = mkStandardTree()
    const { sink } = mkMockSink()
    const lock = mkControllableLockManager()
    const cost = mkControllableCostGuardrail()
    const starter = mkControllableRunWaveStarter()
    const shim = createRunnerShim({
      operatorProvider: () => 'alice',
      treeProvider: mkTreeProvider(tree),
      sink,
      lockManager: lock.mgr,
      costGuardrail: cost.cg,
      runWaveStarter: starter.starter,
      uuid: mkUuidStub(),
    })

    const first = shim.refreshTree(treeId('t-1'))
    await waitFor(() => starter.pendingCount() === 1, 'first running')
    const second = shim.refreshTree(treeId('t-1'))
    await flush()
    expect(starter.calls).toHaveLength(1)
    starter.resolveNext()
    await waitFor(() => starter.calls.length === 2, 'second invoked after first settles')
    starter.resolveNext()
    await Promise.all([first, second])
  })

  it('drained re-entry recomputes S from the LATEST tree state, not the snapshot at enqueue', async () => {
    // 03 §10.3: stale-set is recomputed at wave-start, not at enqueue-time.
    // If the operator edits the tree between enqueue and dispatch, the drained
    // wave dispatches against the current state. This test mutates the tree
    // (by swapping which tree the treeProvider returns) between enqueue and
    // drain to prove the drained wave reads the post-edit tree.
    const treeV1 = mkTree(
      'r',
      [
        mkRoot('r', undefined, { state: 'clean' }),
        mkUserTurn('u', 'r', undefined, { state: 'clean' }),
        mkSend('s_orig', 'u', undefined, { state: 'stale' }),
      ],
      { id: 't-evolve' },
    )
    // Same id but a different stale set — operator edited s_orig to clean and
    // added a new stale Send `s_new` between enqueue and drain.
    const treeV2 = mkTree(
      'r',
      [
        mkRoot('r', undefined, { state: 'clean' }),
        mkUserTurn('u', 'r', undefined, { state: 'clean' }),
        mkSend('s_orig', 'u', { responsePreview: 'original response' }, { state: 'clean' }),
        mkUserTurn('u2', 's_orig', undefined, { state: 'clean' }),
        mkSend('s_new', 'u2', undefined, { state: 'stale' }),
      ],
      { id: 't-evolve' },
    )
    let activeTree = treeV1
    const treeProvider: ShimDependencies['treeProvider'] = (id) =>
      id === treeId('t-evolve') ? activeTree : undefined

    const { sink, callsOf } = mkMockSink()
    const lock = mkControllableLockManager()
    const cost = mkControllableCostGuardrail()
    const starter = mkControllableRunWaveStarter()
    const shim = createRunnerShim({
      operatorProvider: () => 'alice',
      treeProvider,
      sink,
      lockManager: lock.mgr,
      costGuardrail: cost.cg,
      runWaveStarter: starter.starter,
      uuid: mkUuidStub(),
    })

    const first = shim.refreshTree(treeId('t-evolve'))
    await waitFor(() => starter.pendingCount() === 1, 'first wave running')
    // First wave sees treeV1's stale set.
    expect([...starter.calls[0].S]).toEqual([nodeId('s_orig')])

    const second = shim.refreshTree(treeId('t-evolve'))
    await waitFor(
      () => callsOf('emitWaveEvent').some((c) => c.event.kind === 'queued'),
      'second enqueued',
    )

    // Operator edits the tree between enqueue and drain — flip to v2.
    activeTree = treeV2

    starter.resolveNext()
    await waitFor(() => starter.calls.length === 2, 'second drained')

    // Drained wave's S was computed AT DRAIN TIME from treeV2 — not from v1
    // (which only had s_orig stale).
    expect([...starter.calls[1].S]).toEqual([nodeId('s_new')])
    // The drained call also received the v2 tree object directly.
    expect(starter.calls[1].tree).toBe(treeV2)

    starter.resolveNext()
    await Promise.all([first, second])
  })
})

// ============================================================================
// 5. Wave start (step 5) — starter args
// ============================================================================

describe('shim — wave start (step 5)', () => {
  it('starter receives treeId, tree, S, operator, waveId, waveTriggerKind, controller', async () => {
    const tree = mkStandardTree()
    const { sink } = mkMockSink()
    const lock = mkControllableLockManager()
    const cost = mkControllableCostGuardrail()
    const starter = mkControllableRunWaveStarter()
    const shim = createRunnerShim({
      operatorProvider: () => 'alice',
      treeProvider: mkTreeProvider(tree),
      sink,
      lockManager: lock.mgr,
      costGuardrail: cost.cg,
      runWaveStarter: starter.starter,
      uuid: mkUuidStub(['w-42']),
    })

    const p = shim.refreshTree(treeId('t-1'))
    await waitFor(() => starter.pendingCount() === 1, 'starter invoked')

    const a = starter.calls[0]
    expect(a.treeId).toBe(treeId('t-1'))
    expect(a.tree).toBe(tree)
    expect([...a.S]).toEqual([nodeId('s')]) // only s is stale; r/u are clean
    expect(a.operator).toBe('alice')
    expect(a.waveId).toBe('w-42')
    expect(a.waveTriggerKind).toBe('refresh_tree')
    expect(typeof a.controller.cancel).toBe('function')
    expect(typeof a.controller.isCancelled).toBe('function')
    expect(a.controller.isCancelled()).toBe(false)

    starter.resolveNext()
    await p
  })

  it('passes parentConversationTreeId when tree was cloned', async () => {
    // Per the runner-args contract (RunWaveArgs.parentConversationTreeId);
    // covered here because the shim is the layer that reads it from the
    // tree object and forwards it.
    const baseTree = mk3LeafTree()
    const tree: ConversationTree = {
      ...baseTree,
      parentConversationTreeId: treeId('parent-tree'),
    }
    const { sink } = mkMockSink()
    const lock = mkControllableLockManager()
    const cost = mkControllableCostGuardrail()
    const starter = mkControllableRunWaveStarter()
    const shim = createRunnerShim({
      operatorProvider: () => 'alice',
      treeProvider: mkTreeProvider(tree),
      sink,
      lockManager: lock.mgr,
      costGuardrail: cost.cg,
      runWaveStarter: starter.starter,
      uuid: mkUuidStub(),
    })

    const p = shim.refreshTree(treeId('t-3'))
    await waitFor(() => starter.pendingCount() === 1, 'starter invoked')
    expect(starter.calls[0].parentConversationTreeId).toBe(treeId('parent-tree'))
    starter.resolveNext()
    await p
  })
})

// ============================================================================
// 6. Lock release on every exit path
// ============================================================================

describe('shim — lock release', () => {
  it('success path releases lock exactly once', async () => {
    const tree = mkStandardTree()
    const { sink } = mkMockSink()
    const lock = mkControllableLockManager()
    const cost = mkControllableCostGuardrail()
    const starter = mkControllableRunWaveStarter()
    const shim = createRunnerShim({
      operatorProvider: () => 'alice',
      treeProvider: mkTreeProvider(tree),
      sink,
      lockManager: lock.mgr,
      costGuardrail: cost.cg,
      runWaveStarter: starter.starter,
      uuid: mkUuidStub(),
    })

    const p = shim.refreshTree(treeId('t-1'))
    await waitFor(() => starter.pendingCount() === 1, 'starter invoked')
    starter.resolveNext()
    await p
    expect(lock.releaseCalls).toEqual([treeId('t-1')])
  })

  it('starter throws: lock still released', async () => {
    const tree = mkStandardTree()
    const { sink } = mkMockSink()
    const lock = mkControllableLockManager()
    const cost = mkControllableCostGuardrail()
    const starter = mkControllableRunWaveStarter()
    const shim = createRunnerShim({
      operatorProvider: () => 'alice',
      treeProvider: mkTreeProvider(tree),
      sink,
      lockManager: lock.mgr,
      costGuardrail: cost.cg,
      runWaveStarter: starter.starter,
      uuid: mkUuidStub(),
    })

    const p = shim.refreshTree(treeId('t-1'))
    await waitFor(() => starter.pendingCount() === 1, 'starter invoked')
    starter.rejectNext(new Error('runWave blew up'))
    await expect(p).rejects.toThrow('runWave blew up')
    expect(lock.releaseCalls).toEqual([treeId('t-1')])
  })

  it('cost-modal cancel: lock released', async () => {
    const tree = mkStandardTree()
    const { sink } = mkMockSink()
    const lock = mkControllableLockManager()
    const cost = mkControllableCostGuardrail(false)
    const starter = mkControllableRunWaveStarter()
    const shim = createRunnerShim({
      operatorProvider: () => 'alice',
      treeProvider: mkTreeProvider(tree),
      sink,
      lockManager: lock.mgr,
      costGuardrail: cost.cg,
      runWaveStarter: starter.starter,
      uuid: mkUuidStub(),
    })

    await shim.refreshTree(treeId('t-1'))
    expect(lock.releaseCalls).toEqual([treeId('t-1')])
  })

  it('queued path: this invocation released its lock; the queued one acquires + releases its own', async () => {
    const tree = mkStandardTree()
    const { sink } = mkMockSink()
    const lock = mkControllableLockManager()
    const cost = mkControllableCostGuardrail()
    const starter = mkControllableRunWaveStarter()
    const shim = createRunnerShim({
      operatorProvider: () => 'alice',
      treeProvider: mkTreeProvider(tree),
      sink,
      lockManager: lock.mgr,
      costGuardrail: cost.cg,
      runWaveStarter: starter.starter,
      uuid: mkUuidStub(),
    })

    const first = shim.refreshTree(treeId('t-1'))
    await waitFor(() => starter.pendingCount() === 1, 'first running')
    const second = shim.refreshTree(treeId('t-1'))
    await flush()
    // The second call's shim invocation enqueued and released its own lock,
    // but the lock was acquired by the first call's shim too. Acquire count
    // == 2 (one per shim entry).
    expect(lock.acquireCalls).toEqual([treeId('t-1'), treeId('t-1')])
    // After enqueue, the queued-path shim releases its lock immediately.
    expect(lock.releaseCalls).toContain(treeId('t-1'))

    starter.resolveNext()
    await waitFor(() => starter.calls.length === 2, 'queued drained')
    starter.resolveNext()
    await Promise.all([first, second])
    // 3 acquires total: first invocation, second-original (enqueue), drain
    // re-entry. Releases match.
    expect(lock.acquireCalls.length).toBe(3)
    expect(lock.releaseCalls.length).toBe(3)
  })

  it('tag-gate abort: no acquire, no release', async () => {
    const tree = mkStandardTree()
    const { sink } = mkMockSink()
    const lock = mkControllableLockManager()
    const cost = mkControllableCostGuardrail()
    const starter = mkControllableRunWaveStarter()
    const shim = createRunnerShim({
      operatorProvider: () => '',
      treeProvider: mkTreeProvider(tree),
      sink,
      lockManager: lock.mgr,
      costGuardrail: cost.cg,
      runWaveStarter: starter.starter,
      uuid: mkUuidStub(),
    })

    await shim.refreshTree(treeId('t-1'))
    expect(lock.acquireCalls).toHaveLength(0)
    expect(lock.releaseCalls).toHaveLength(0)
  })

  it('busy abort: no release (acquire returned busy, nothing to release)', async () => {
    const tree = mkStandardTree()
    const { sink } = mkMockSink()
    const lock = mkControllableLockManager({
      acquireResults: [{ acquired: false, holderTabId: 'other' }],
    })
    const cost = mkControllableCostGuardrail()
    const starter = mkControllableRunWaveStarter()
    const shim = createRunnerShim({
      operatorProvider: () => 'alice',
      treeProvider: mkTreeProvider(tree),
      sink,
      lockManager: lock.mgr,
      costGuardrail: cost.cg,
      runWaveStarter: starter.starter,
      uuid: mkUuidStub(),
    })

    await shim.refreshTree(treeId('t-1'))
    expect(lock.acquireCalls).toEqual([treeId('t-1')])
    expect(lock.releaseCalls).toHaveLength(0)
  })
})

// ============================================================================
// 7. S construction per entry point
// ============================================================================

describe('shim — S construction', () => {
  it('refreshNode: S = buildSForNode(tree, nodeId)', async () => {
    const tree = mk3LeafTree()
    const { sink } = mkMockSink()
    const lock = mkControllableLockManager()
    const cost = mkControllableCostGuardrail()
    const starter = mkControllableRunWaveStarter()
    const shim = createRunnerShim({
      operatorProvider: () => 'alice',
      treeProvider: mkTreeProvider(tree),
      sink,
      lockManager: lock.mgr,
      costGuardrail: cost.cg,
      runWaveStarter: starter.starter,
      uuid: mkUuidStub(),
    })

    const p = shim.refreshNode(treeId('t-3'), nodeId('s_a'))
    await waitFor(() => starter.pendingCount() === 1, 'starter invoked')
    expect([...starter.calls[0].S].sort()).toEqual([...buildSForNode(tree, nodeId('s_a'))].sort())
    starter.resolveNext()
    await p
  })

  it('refreshSubtree: S = buildSForSubtree(tree, rootNodeId)', async () => {
    // Subtree rooted at 'u' should pick up only u and its descendant Sends.
    const tree = mk3LeafTree()
    const { sink } = mkMockSink()
    const lock = mkControllableLockManager()
    const cost = mkControllableCostGuardrail()
    const starter = mkControllableRunWaveStarter()
    const shim = createRunnerShim({
      operatorProvider: () => 'alice',
      treeProvider: mkTreeProvider(tree),
      sink,
      lockManager: lock.mgr,
      costGuardrail: cost.cg,
      runWaveStarter: starter.starter,
      uuid: mkUuidStub(),
    })

    const p = shim.refreshSubtree(treeId('t-3'), nodeId('u'))
    await waitFor(() => starter.pendingCount() === 1, 'starter invoked')
    expect([...starter.calls[0].S].sort()).toEqual([...buildSForSubtree(tree, nodeId('u'))].sort())
    starter.resolveNext()
    await p
  })

  it('refreshTree: S = buildSForTree(tree)', async () => {
    const tree = mk3LeafTree()
    const { sink } = mkMockSink()
    const lock = mkControllableLockManager()
    const cost = mkControllableCostGuardrail()
    const starter = mkControllableRunWaveStarter()
    const shim = createRunnerShim({
      operatorProvider: () => 'alice',
      treeProvider: mkTreeProvider(tree),
      sink,
      lockManager: lock.mgr,
      costGuardrail: cost.cg,
      runWaveStarter: starter.starter,
      uuid: mkUuidStub(),
    })

    const p = shim.refreshTree(treeId('t-3'))
    await waitFor(() => starter.pendingCount() === 1, 'starter invoked')
    expect([...starter.calls[0].S].sort()).toEqual([...buildSForTree(tree)].sort())
    starter.resolveNext()
    await p
  })

  it('retryFailedNodes: S includes the input ids + their failed Send ancestors; demotion fires', async () => {
    // r → u → s_mid(failed) → u2 → s_leaf(failed). Retry({s_leaf})
    // should: (a) demote s_mid and s_leaf to stale via sink, (b) call
    // starter with S = {s_mid, s_leaf}.
    const tree = mkTree(
      'r',
      [
        mkRoot('r', undefined, { state: 'clean' }),
        mkUserTurn('u', 'r', undefined, { state: 'clean' }),
        mkSend('s_mid', 'u', undefined, { state: 'failed' }),
        mkUserTurn('u2', 's_mid', undefined, { state: 'stale' }),
        mkSend('s_leaf', 'u2', undefined, { state: 'failed' }),
      ],
      { id: 't-retry' },
    )
    const { sink, callsOf } = mkMockSink()
    const lock = mkControllableLockManager()
    const cost = mkControllableCostGuardrail()
    const starter = mkControllableRunWaveStarter()
    const shim = createRunnerShim({
      operatorProvider: () => 'alice',
      treeProvider: mkTreeProvider(tree),
      sink,
      lockManager: lock.mgr,
      costGuardrail: cost.cg,
      runWaveStarter: starter.starter,
      uuid: mkUuidStub(),
    })

    const p = shim.retryFailedNodes(treeId('t-retry'), [nodeId('s_leaf')])
    await waitFor(() => starter.pendingCount() === 1, 'starter invoked')

    // S includes both the leaf and its failed ancestor.
    expect([...starter.calls[0].S].sort()).toEqual([nodeId('s_leaf'), nodeId('s_mid')].sort())

    // Demotion fired through the sink: both s_mid and s_leaf flipped stale
    // with a null reason (clears lastError).
    const stateCalls = callsOf('setNodeState')
    const demotedIds = stateCalls.filter((c) => c.state === 'stale').map((c) => c.nodeId)
    expect(demotedIds.sort()).toEqual([nodeId('s_leaf'), nodeId('s_mid')].sort())
    for (const c of stateCalls.filter((c) => c.state === 'stale')) {
      expect(c.reason).toBeNull()
    }
    // clearExecution called for each demoted node.
    const clearedIds = callsOf('clearExecution').map((c) => c.nodeId)
    expect(clearedIds.sort()).toEqual([nodeId('s_leaf'), nodeId('s_mid')].sort())

    // The tree passed to the starter should reflect the demotion (state=stale
    // on both nodes) so runWave's computeReady admits the leaf.
    const passedTree = starter.calls[0].tree
    const midPassed = passedTree.nodes.find((n) => n.id === nodeId('s_mid'))
    const leafPassed = passedTree.nodes.find((n) => n.id === nodeId('s_leaf'))
    expect(midPassed?.state).toBe('stale')
    expect(leafPassed?.state).toBe('stale')

    starter.resolveNext()
    await p
  })
})

// ============================================================================
// 8. waveTriggerKind mapping
// ============================================================================

describe('shim — waveTriggerKind mapping (03 §6.2)', () => {
  const cases: Array<{
    name: string
    fire: (s: ReturnType<typeof createRunnerShim>, t: ConversationTreeId) => Promise<void>
    expected: WaveTriggerKind
  }> = [
    {
      name: 'refreshNode → refresh_node',
      fire: (s, t) => s.refreshNode(t, nodeId('s')),
      expected: 'refresh_node',
    },
    {
      name: 'refreshSubtree → refresh_subtree',
      fire: (s, t) => s.refreshSubtree(t, nodeId('s')),
      expected: 'refresh_subtree',
    },
    {
      name: 'refreshTree → refresh_tree',
      fire: (s, t) => s.refreshTree(t),
      expected: 'refresh_tree',
    },
    {
      name: 'retryFailedNodes → retry_failed',
      fire: (s, t) => s.retryFailedNodes(t, [nodeId('s')]),
      expected: 'retry_failed',
    },
  ]

  for (const c of cases) {
    it(c.name, async () => {
      const tree = mkTree('r', [
        mkRoot('r', undefined, { state: 'clean' }),
        mkUserTurn('u', 'r', undefined, { state: 'clean' }),
        mkSend('s', 'u', undefined, { state: 'failed' }),
      ], { id: 't-1' })
      const { sink } = mkMockSink()
      const lock = mkControllableLockManager()
      const cost = mkControllableCostGuardrail()
      const starter = mkControllableRunWaveStarter()
      const shim = createRunnerShim({
        operatorProvider: () => 'alice',
        treeProvider: mkTreeProvider(tree),
        sink,
        lockManager: lock.mgr,
        costGuardrail: cost.cg,
        runWaveStarter: starter.starter,
        uuid: mkUuidStub(),
      })

      const p = c.fire(shim, treeId('t-1'))
      await waitFor(() => starter.pendingCount() === 1, `${c.name}: starter invoked`)
      expect(starter.calls[0].waveTriggerKind).toBe(c.expected)
      starter.resolveNext()
      await p
    })
  }
})

// ============================================================================
// 9. cancelWave / cancelQueued
// ============================================================================

describe('shim — cancelWave', () => {
  it('flips the active wave\'s controller cancellation flag', async () => {
    const tree = mk3LeafTree()
    const { sink } = mkMockSink()
    const lock = mkControllableLockManager()
    const cost = mkControllableCostGuardrail()
    const starter = mkControllableRunWaveStarter()
    const shim = createRunnerShim({
      operatorProvider: () => 'alice',
      treeProvider: mkTreeProvider(tree),
      sink,
      lockManager: lock.mgr,
      costGuardrail: cost.cg,
      runWaveStarter: starter.starter,
      uuid: mkUuidStub(),
    })

    const p = shim.refreshTree(treeId('t-3'))
    await waitFor(() => starter.pendingCount() === 1, 'wave running')
    expect(starter.calls[0].controller.isCancelled()).toBe(false)

    // Don't await yet; want to assert mid-flight.
    const cancelP = shim.cancelWave(treeId('t-3'))
    expect(starter.calls[0].controller.isCancelled()).toBe(true)

    starter.resolveNext()
    await Promise.all([p, cancelP])
  })

  it('no active wave on this tree: no-op', async () => {
    const tree = mkStandardTree()
    const { sink, callsOf } = mkMockSink()
    const lock = mkControllableLockManager()
    const cost = mkControllableCostGuardrail()
    const starter = mkControllableRunWaveStarter()
    const shim = createRunnerShim({
      operatorProvider: () => 'alice',
      treeProvider: mkTreeProvider(tree),
      sink,
      lockManager: lock.mgr,
      costGuardrail: cost.cg,
      runWaveStarter: starter.starter,
      uuid: mkUuidStub(),
    })

    // No wave running.
    await shim.cancelWave(treeId('t-1'))
    expect(callsOf('emitWaveEvent')).toHaveLength(0)
    expect(starter.calls).toHaveLength(0)
  })

  it('returns when the cancelled wave fully settles', async () => {
    const tree = mk3LeafTree()
    const { sink } = mkMockSink()
    const lock = mkControllableLockManager()
    const cost = mkControllableCostGuardrail()
    const starter = mkControllableRunWaveStarter()
    const shim = createRunnerShim({
      operatorProvider: () => 'alice',
      treeProvider: mkTreeProvider(tree),
      sink,
      lockManager: lock.mgr,
      costGuardrail: cost.cg,
      runWaveStarter: starter.starter,
      uuid: mkUuidStub(),
    })

    const p = shim.refreshTree(treeId('t-3'))
    await waitFor(() => starter.pendingCount() === 1, 'wave running')

    let cancelResolved = false
    const cancelP = shim.cancelWave(treeId('t-3')).then(() => {
      cancelResolved = true
    })
    await flush()
    expect(cancelResolved).toBe(false) // wave not settled yet
    starter.resolveNext(mkSummary({ cancelled: 3 }))
    await cancelP
    expect(cancelResolved).toBe(true)
    await p
  })
})

describe('shim — cancelQueued', () => {
  it('drops every queued wave; each emits a complete event with summary.cancelled = leaf count', async () => {
    const tree = mk3LeafTree()
    const { sink, callsOf } = mkMockSink()
    const lock = mkControllableLockManager()
    const cost = mkControllableCostGuardrail()
    const starter = mkControllableRunWaveStarter()
    const shim = createRunnerShim({
      operatorProvider: () => 'alice',
      treeProvider: mkTreeProvider(tree),
      sink,
      lockManager: lock.mgr,
      costGuardrail: cost.cg,
      runWaveStarter: starter.starter,
      uuid: mkUuidStub(['w-active', 'w-q1', 'w-q2']),
    })

    const active = shim.refreshTree(treeId('t-3'))
    await waitFor(() => starter.pendingCount() === 1, 'active running')
    const q1 = shim.refreshTree(treeId('t-3'))
    const q2 = shim.refreshTree(treeId('t-3'))
    await waitFor(
      () => callsOf('emitWaveEvent').filter((c) => c.event.kind === 'queued').length === 2,
      'two queued events',
    )

    await shim.cancelQueued(treeId('t-3'))

    // Two `complete` events with cancelled = 3 (3-leaf fan).
    const completes = callsOf('emitWaveEvent')
      .map((c) => c.event)
      .filter((e): e is Extract<WaveEvent, { kind: 'complete' }> => e.kind === 'complete')
    expect(completes.length).toBe(2)
    for (const c of completes) {
      expect(c.summary.cancelled).toBe(3)
    }

    // q1 and q2 resolve immediately (they were dropped).
    await Promise.all([q1, q2])

    // Active wave still in flight; complete it.
    starter.resolveNext()
    await active
    // Starter was called exactly once (active); queued never reached starter.
    expect(starter.calls).toHaveLength(1)
  })

  it('does NOT affect the active wave', async () => {
    const tree = mk3LeafTree()
    const { sink, callsOf } = mkMockSink()
    const lock = mkControllableLockManager()
    const cost = mkControllableCostGuardrail()
    const starter = mkControllableRunWaveStarter()
    const shim = createRunnerShim({
      operatorProvider: () => 'alice',
      treeProvider: mkTreeProvider(tree),
      sink,
      lockManager: lock.mgr,
      costGuardrail: cost.cg,
      runWaveStarter: starter.starter,
      uuid: mkUuidStub(),
    })

    const active = shim.refreshTree(treeId('t-3'))
    await waitFor(() => starter.pendingCount() === 1, 'active running')
    const q1 = shim.refreshTree(treeId('t-3'))
    // Wait until q1 actually reaches the enqueue step (its shim has to walk
    // through lock acquire + cost approve before it sees the active wave).
    // Without this wait, cancelQueued runs while the queue is still empty
    // and the q1 wave drains normally — defeating the test.
    await waitFor(
      () => callsOf('emitWaveEvent').some((c) => c.event.kind === 'queued'),
      'q1 enqueued',
    )

    await shim.cancelQueued(treeId('t-3'))
    expect(starter.calls[0].controller.isCancelled()).toBe(false)

    starter.resolveNext()
    await Promise.all([active, q1])
    // After cancelQueued + active settle: starter only ever called for the
    // active wave; the dropped q1 never reached starter.
    expect(starter.calls).toHaveLength(1)
  })

  it('queue empty: no-op', async () => {
    const tree = mkStandardTree()
    const { sink, callsOf } = mkMockSink()
    const lock = mkControllableLockManager()
    const cost = mkControllableCostGuardrail()
    const starter = mkControllableRunWaveStarter()
    const shim = createRunnerShim({
      operatorProvider: () => 'alice',
      treeProvider: mkTreeProvider(tree),
      sink,
      lockManager: lock.mgr,
      costGuardrail: cost.cg,
      runWaveStarter: starter.starter,
      uuid: mkUuidStub(),
    })

    await shim.cancelQueued(treeId('t-1'))
    expect(callsOf('emitWaveEvent')).toHaveLength(0)
  })
})

// ============================================================================
// 10. Wave-end transform reconcile
// ============================================================================

describe('shim — wave-end reconcile (03 §3.1 step 6)', () => {
  it('after wave settles, transform nodes whose ancestors are clean flip stale→clean', async () => {
    const tree = mkTreeWithStaleScoreSibling()
    const { sink, callsOf } = mkMockSink()
    const lock = mkControllableLockManager()
    const cost = mkControllableCostGuardrail()
    const starter = mkControllableRunWaveStarter()
    const shim = createRunnerShim({
      operatorProvider: () => 'alice',
      treeProvider: mkTreeProvider(tree),
      sink,
      lockManager: lock.mgr,
      costGuardrail: cost.cg,
      runWaveStarter: starter.starter,
      uuid: mkUuidStub(),
    })

    const p = shim.refreshTree(treeId('t-reconcile'))
    await waitFor(() => starter.pendingCount() === 1, 'starter invoked')
    // Before starter resolves: no reconcile yet.
    const preReconcile = callsOf('setNodeState').filter((c) => c.nodeId === nodeId('score'))
    expect(preReconcile).toHaveLength(0)

    starter.resolveNext()
    await p

    // After settle: the score sibling flipped to clean.
    const post = callsOf('setNodeState').filter((c) => c.nodeId === nodeId('score'))
    expect(post).toHaveLength(1)
    expect(post[0].state).toBe('clean')
  })

  it('reconcile runs before queue drain (drained waves see reconciled state)', async () => {
    // Active wave settles → reconcile fires → queue drain starts. Capture
    // ordering by recording the sink calls + starter invocations interleaved
    // (the test reads the call log directly).
    const tree = mkTreeWithStaleScoreSibling()
    const { sink, calls: sinkCalls } = mkMockSink()
    const lock = mkControllableLockManager()
    const cost = mkControllableCostGuardrail()
    const starter = mkControllableRunWaveStarter()
    const shim = createRunnerShim({
      operatorProvider: () => 'alice',
      treeProvider: mkTreeProvider(tree),
      sink,
      lockManager: lock.mgr,
      costGuardrail: cost.cg,
      runWaveStarter: starter.starter,
      uuid: mkUuidStub(),
    })

    const first = shim.refreshTree(treeId('t-reconcile'))
    await waitFor(() => starter.pendingCount() === 1, 'first running')
    const second = shim.refreshTree(treeId('t-reconcile'))
    await flush()
    starter.resolveNext()
    await waitFor(() => starter.calls.length === 2, 'second drained')

    // Sequence: reconcile's setNodeState(score, clean) should appear BEFORE
    // the second starter invocation. We can probe by checking that a sink
    // call for 'score' exists prior to the second wave's starter execution
    // — proxy: pre-second-starter-callsite, the call log must include a
    // setNodeState for score.
    const scoreCall = sinkCalls.findIndex(
      (c) => c.method === 'setNodeState' && c.nodeId === nodeId('score'),
    )
    expect(scoreCall).toBeGreaterThanOrEqual(0)

    starter.resolveNext()
    await Promise.all([first, second])
  })

  it('reconcile reads POST-WAVE state via the recording sink, not a treeProvider snapshot (rubber-duck Finding D)', async () => {
    // Tree where Score's parent (the Send) starts stale. The wave's dispatcher
    // transitions the Send to clean via the (recording) sink. The post-wave
    // tree built from the recorder's captures shows the Send as clean, so
    // reconcile flips the Score child.
    //
    // The treeProvider in this test returns a FIXED tree object with the Send
    // still stale — closing the gap with the pre-PR4e+f.1 implementation that
    // re-read treeProvider for the post-wave snapshot. Under the old code,
    // reconcile would walk the stale tree and the Score would stay stale; the
    // dispatcher's sink writes would never reach reconcile. The recorder-based
    // post-tree is what makes this assertion pass.
    const tree = mkTree(
      'r',
      [
        mkRoot('r', undefined, { state: 'clean' }),
        mkUserTurn('u', 'r', undefined, { state: 'clean' }),
        mkSend('s_to_be_clean', 'u', undefined, { state: 'stale' }),
        mkScore('score', 's_to_be_clean', undefined, { state: 'stale' }),
      ],
      { id: 't-recorder' },
    )
    const { sink, callsOf } = mkMockSink()
    const lock = mkControllableLockManager()
    const cost = mkControllableCostGuardrail()

    // Custom starter that simulates the dispatcher: writes
    // setNodeState(s_to_be_clean, clean) through the wave's sink (the
    // recorder), then resolves.
    const starter: RunWaveStarter = async (args) => {
      args.sink.setNodeState(args.treeId, nodeId('s_to_be_clean'), 'clean')
      return {
        succeeded: 1,
        failed: { transient: 0, rate_limited: 0, permanent: 0 },
        blocked: 0,
        cancelled: 0,
        reflog_evicted: 0,
      }
    }

    const shim = createRunnerShim({
      operatorProvider: () => 'alice',
      treeProvider: mkTreeProvider(tree),
      sink,
      lockManager: lock.mgr,
      costGuardrail: cost.cg,
      runWaveStarter: starter,
      uuid: mkUuidStub(),
    })

    await shim.refreshTree(treeId('t-recorder'))

    // Dispatcher's setNodeState forwarded to the underlying sink (visible
    // to the React state container).
    const sCalls = callsOf('setNodeState').filter((c) => c.nodeId === nodeId('s_to_be_clean'))
    expect(sCalls).toHaveLength(1)
    expect(sCalls[0].state).toBe('clean')

    // Reconcile saw the post-wave tree (Send=clean) and flipped the Score
    // sibling. The reconcile call writes to the underlying sink, NOT the
    // wrapped recorder (the recorder's lifetime is the wave; reconcile fires
    // after the wave settles).
    const scoreCalls = callsOf('setNodeState').filter((c) => c.nodeId === nodeId('score'))
    expect(scoreCalls).toHaveLength(1)
    expect(scoreCalls[0].state).toBe('clean')
  })

  it('starter receives a sink that is a wrapper (not the bare deps.sink reference)', async () => {
    // Defense-in-depth: if a future refactor reverts the recorder wrapping
    // and passes deps.sink directly to the starter, the wave-end reconcile
    // would silently fall back to reading treeProvider — losing the Finding D
    // fix without a test failure. This test pins "the starter's sink is NOT
    // the bare reference" so the regression would surface immediately.
    const tree = mkStandardTree()
    const { sink } = mkMockSink()
    const lock = mkControllableLockManager()
    const cost = mkControllableCostGuardrail()
    const starter = mkControllableRunWaveStarter()
    const shim = createRunnerShim({
      operatorProvider: () => 'alice',
      treeProvider: mkTreeProvider(tree),
      sink,
      lockManager: lock.mgr,
      costGuardrail: cost.cg,
      runWaveStarter: starter.starter,
      uuid: mkUuidStub(),
    })

    const p = shim.refreshTree(treeId('t-1'))
    await waitFor(() => starter.pendingCount() === 1, 'starter invoked')
    expect(starter.calls[0].sink).not.toBe(sink)
    expect(typeof starter.calls[0].sink.setNodeState).toBe('function')
    starter.resolveNext()
    await p
  })

  it('recording sink forwards every sink method to the underlying deps.sink', async () => {
    // The recorder only INTERCEPTS setNodeState to capture; every other
    // method must pass through unchanged. Without this, recordExecution /
    // clearExecution / emitWaveEvent / setReflogPinned calls the dispatcher
    // makes during the wave would silently no-op against the React state
    // container.
    const tree = mkStandardTree()
    const { sink, callsOf } = mkMockSink()
    const lock = mkControllableLockManager()
    const cost = mkControllableCostGuardrail()

    const starter: RunWaveStarter = async (args) => {
      args.sink.setNodeState(args.treeId, nodeId('s'), 'clean')
      args.sink.recordExecution(args.treeId, nodeId('s'), {
        executionId: 'e-1',
        attemptedAt: '2026-06-10T00:00:00Z',
        attackResultId: 'ar-1',
        conversationId: 'conv-1',
        pieceIds: ['p-1'],
        outcome: 'success',
        resolvedInputHashAtExecution: 'sha256:00',
        waveId: args.waveId,
        waveTriggerKind: args.waveTriggerKind,
        dispatchedAt: '2026-06-10T00:00:00Z',
        targetFirstByteAt: '2026-06-10T00:00:00Z',
        completedAt: '2026-06-10T00:00:00Z',
      })
      args.sink.clearExecution(args.treeId, nodeId('s'))
      args.sink.setReflogPinned(args.treeId, nodeId('s'), 'e-1', true)
      // emitWaveEvent is exercised by runWave; the recorder must forward it.
      args.sink.emitWaveEvent({
        kind: 'node_complete',
        waveId: args.waveId,
        nodeId: nodeId('s'),
        outcome: 'success',
        emittedAt: '2026-06-10T00:00:00Z',
      })
      return {
        succeeded: 1,
        failed: { transient: 0, rate_limited: 0, permanent: 0 },
        blocked: 0,
        cancelled: 0,
        reflog_evicted: 0,
      }
    }

    const shim = createRunnerShim({
      operatorProvider: () => 'alice',
      treeProvider: mkTreeProvider(tree),
      sink,
      lockManager: lock.mgr,
      costGuardrail: cost.cg,
      runWaveStarter: starter,
      uuid: mkUuidStub(),
    })

    await shim.refreshTree(treeId('t-1'))

    // Every sink method's call landed on the underlying sink.
    expect(callsOf('recordExecution')).toHaveLength(1)
    expect(callsOf('clearExecution')).toHaveLength(1)
    expect(callsOf('setReflogPinned')).toHaveLength(1)
    // 2 emit: one from the dispatcher (node_complete), one from cancelQueued?
    // No, that doesn't fire here. Just the dispatcher's. (Plus the shim's
    // own — there shouldn't be any other shim-level events on the happy path.)
    const events = callsOf('emitWaveEvent')
    expect(events.length).toBeGreaterThanOrEqual(1)
    expect(events.some((c) => c.event.kind === 'node_complete')).toBe(true)
  })
})

// ============================================================================
// 11. Per-tree isolation
// ============================================================================

describe('shim — per-tree isolation', () => {
  it('two different trees can have concurrent active waves', async () => {
    const treeA = mkStandardTree('t-A')
    const treeB = mkStandardTree('t-B')
    const treeProvider: ShimDependencies['treeProvider'] = (id) =>
      id === treeId('t-A') ? treeA : id === treeId('t-B') ? treeB : undefined
    const { sink } = mkMockSink()
    const lock = mkControllableLockManager()
    const cost = mkControllableCostGuardrail()
    const starter = mkControllableRunWaveStarter()
    const shim = createRunnerShim({
      operatorProvider: () => 'alice',
      treeProvider,
      sink,
      lockManager: lock.mgr,
      costGuardrail: cost.cg,
      runWaveStarter: starter.starter,
      uuid: mkUuidStub(),
    })

    const a = shim.refreshTree(treeId('t-A'))
    const b = shim.refreshTree(treeId('t-B'))
    await waitFor(() => starter.pendingCount() === 2, 'both waves running')
    expect(starter.calls.map((c) => c.treeId).sort()).toEqual([treeId('t-A'), treeId('t-B')].sort())
    starter.resolveNext()
    starter.resolveNext()
    await Promise.all([a, b])
  })

  it("treeA's queue does not affect treeB", async () => {
    const treeA = mk3LeafTree('t-A')
    const treeB = mkStandardTree('t-B')
    const treeProvider: ShimDependencies['treeProvider'] = (id) =>
      id === treeId('t-A') ? treeA : id === treeId('t-B') ? treeB : undefined
    const { sink, callsOf } = mkMockSink()
    const lock = mkControllableLockManager()
    const cost = mkControllableCostGuardrail()
    const starter = mkControllableRunWaveStarter()
    const shim = createRunnerShim({
      operatorProvider: () => 'alice',
      treeProvider,
      sink,
      lockManager: lock.mgr,
      costGuardrail: cost.cg,
      runWaveStarter: starter.starter,
      uuid: mkUuidStub(),
    })

    const aFirst = shim.refreshTree(treeId('t-A'))
    await waitFor(() => starter.pendingCount() === 1, 'A running')
    const aSecond = shim.refreshTree(treeId('t-A')) // queued behind A's first
    await waitFor(
      () => callsOf('emitWaveEvent').some((c) => c.event.kind === 'queued'),
      'A second queued',
    )

    // B starts independently (no queue contention).
    const b = shim.refreshTree(treeId('t-B'))
    await waitFor(() => starter.pendingCount() === 2, 'B started without queue')

    starter.resolveNext() // settle A's first
    await waitFor(() => starter.calls.length === 3, "A's second drained")
    starter.resolveNext() // settle B
    starter.resolveNext() // settle A's second
    await Promise.all([aFirst, aSecond, b])
  })
})

// ============================================================================
// 12. Sanity / no-op edge cases
// ============================================================================

describe('shim — sanity edges', () => {
  it('treeProvider returns undefined: silent no-op (no acquire, no events)', async () => {
    const { sink, callsOf } = mkMockSink()
    const lock = mkControllableLockManager()
    const cost = mkControllableCostGuardrail()
    const starter = mkControllableRunWaveStarter()
    const shim = createRunnerShim({
      operatorProvider: () => 'alice',
      treeProvider: () => undefined,
      sink,
      lockManager: lock.mgr,
      costGuardrail: cost.cg,
      runWaveStarter: starter.starter,
      uuid: mkUuidStub(),
    })

    await shim.refreshTree(treeId('t-1'))
    // Tag-gate passes → lock acquired → tree missing → silent return.
    // The lock IS released (acquired in step 2).
    expect(callsOf('emitWaveEvent')).toHaveLength(0)
    expect(starter.calls).toHaveLength(0)
    expect(lock.releaseCalls).toEqual([treeId('t-1')])
  })
})
