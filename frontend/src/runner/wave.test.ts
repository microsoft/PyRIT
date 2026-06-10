// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Tests for `runWave` — the dispatch loop that orchestrates per-leaf
 * `dispatchLeaf` calls across a tree's in-need-of-dispatch set, applies the
 * concurrency cap, runs the in-flight cascade when a leaf's interior Send
 * fails, honors operator cancellation, and emits the wave-event stream.
 *
 * The loop is the runner's central scheduling layer. Tests use a deferred-
 * resolution mock API client so per-leaf timing can be controlled to
 * exercise the concurrency cap, the cascade, and cancellation precisely.
 */

import type {
  AddMessageRequest,
  AddMessageResponse,
  ConversationMessagesResponse,
  CreateAttackRequest,
} from '../types'
import type { ApiError } from '../services/errors'
import type { RunnerAttacksApi } from './dispatch'
import { createWaveController, runWave } from './wave'
import type { WaveSummary } from './wave'
import {
  mkFan,
  mkMockSink,
  mkRoot,
  mkSend,
  mkTree,
  mkUserTurn,
  nodeId,
  treeId,
} from './testHelpers'
import type { ConversationTreeNode, NodeState, WaveEvent } from './treeTypes'

// ============================================================================
// Deferred-resolution mock API
// ============================================================================

interface Deferred {
  resolve: (r: AddMessageResponse) => void
  reject: (e: ApiError) => void
}

interface ControllableApiHandle {
  api: RunnerAttacksApi
  createCalls: CreateAttackRequest[]
  addMessageCalls: Array<{ attackResultId: string; request: AddMessageRequest }>
  /**
   * Inflight tracking. `current` is updated as addMessage calls start and
   * settle; `max` is the running maximum, used to assert the concurrency
   * cap holds throughout the wave.
   */
  inflight: { current: number; max: number }
  /**
   * Release the next pending addMessage with a success response. If there
   * is no pending addMessage, throws (test is racing the loop).
   */
  releaseNext: (response?: AddMessageResponse) => void
  /** Release the next pending addMessage with a failure. */
  failNext: (error: ApiError) => void
  /** How many addMessages are awaiting release right now. */
  pendingCount: () => number
}

function mkControllableApi(): ControllableApiHandle {
  const createCalls: CreateAttackRequest[] = []
  const addMessageCalls: Array<{ attackResultId: string; request: AddMessageRequest }> = []
  const pending: Deferred[] = []
  const inflight = { current: 0, max: 0 }
  let arCounter = 0

  const api: RunnerAttacksApi = {
    createAttack: async (request) => {
      createCalls.push(request)
      arCounter++
      return {
        attack_result_id: `ar-${arCounter}`,
        conversation_id: `conv-${arCounter}`,
        created_at: '2026-06-10T00:00:00Z',
      }
    },
    addMessage: async (attackResultId, request) => {
      addMessageCalls.push({ attackResultId, request })
      inflight.current++
      if (inflight.current > inflight.max) inflight.max = inflight.current
      try {
        return await new Promise<AddMessageResponse>((resolve, reject) => {
          pending.push({ resolve, reject })
        })
      } finally {
        inflight.current--
      }
    },
  }

  const releaseNext = (response?: AddMessageResponse): void => {
    const d = pending.shift()
    if (d === undefined) throw new Error('releaseNext: no pending addMessage')
    d.resolve(response ?? mkAddMessageResponse({ turnNumber: 2, pieceId: 'asst-x' }))
  }
  const failNext = (error: ApiError): void => {
    const d = pending.shift()
    if (d === undefined) throw new Error('failNext: no pending addMessage')
    d.reject(error)
  }

  return { api, createCalls, addMessageCalls, inflight, releaseNext, failNext, pendingCount: () => pending.length }
}

function mkAddMessageResponse(args: { turnNumber: number; pieceId: string }): AddMessageResponse {
  const messages: ConversationMessagesResponse = {
    conversation_id: 'conv-x',
    messages: [
      {
        turn_number: args.turnNumber,
        role: 'assistant',
        pieces: [
          {
            piece_id: args.pieceId,
            original_value_data_type: 'text',
            converted_value_data_type: 'text',
            original_value: 'response',
            converted_value: 'response',
            scores: [],
            response_error: 'none',
            original_prompt_id: args.pieceId,
            converter_identifiers: [],
          },
        ],
        created_at: '2026-06-10T00:00:00Z',
      },
    ],
  }
  return {
    attack: {
      attack_result_id: 'ar-x',
      conversation_id: 'conv-x',
      attack_type: 'manual',
      converters: [],
      message_count: args.turnNumber,
      related_conversation_ids: [],
      labels: {},
      created_at: '2026-06-10T00:00:00Z',
      updated_at: '2026-06-10T00:00:00Z',
    },
    messages,
  }
}

function mkApiError(o: Partial<ApiError> = {}): ApiError {
  return { status: 500, detail: 'boom', isNetworkError: false, isTimeout: false, raw: null, ...o }
}

/**
 * Yield control to the event loop so pending microtasks (promise
 * resolutions, await continuations) get a chance to run. Defaults to 32
 * rounds — enough for the dispatcher's longest chain (await createAttack →
 * await addMessage → record + state-transition → wrap .then → Promise.race
 * resolution → loop iteration → pick next → start dispatch → await
 * createAttack → await addMessage).
 */
async function flushMicrotasks(times = 32): Promise<void> {
  for (let i = 0; i < times; i++) {
    await Promise.resolve()
  }
}

/**
 * Poll-based wait. Reschedules on the microtask queue up to `maxAttempts`
 * times, checking `predicate` each time. Throws if the predicate never
 * matches — better than a flaky test that races the loop.
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
  throw new Error(`waitFor: ${description} never satisfied after ${maxAttempts} microtask hops`)
}

// ============================================================================
// Standard wave context
// ============================================================================

const STANDARD = {
  treeId: treeId('t-1'),
  operator: 'alice',
  operation: 'op-1',
  waveId: 'wave-uuid-1',
  waveTriggerKind: 'refresh_tree' as const,
  parentConversationTreeId: null,
}

// ============================================================================
// 1. Empty S / no-op wave
// ============================================================================

describe('runWave — empty S', () => {
  it('emits start and complete with a zero summary; no API calls', async () => {
    const tree = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r'), mkSend('s', 'u', undefined, { state: 'clean' })])
    const { sink, events } = mkMockSinkPlus()
    const { api, createCalls, addMessageCalls } = mkControllableApi()

    const summary = await runWave({
      ...STANDARD,
      tree,
      S: new Set(),
      sink,
      api,
    })

    expect(createCalls).toHaveLength(0)
    expect(addMessageCalls).toHaveLength(0)
    expect(events().map((e) => e.kind)).toEqual(['start', 'complete'])
    expect(summary).toEqual(emptySummary())
  })
})

// ============================================================================
// 2. Single leaf happy path
// ============================================================================

describe('runWave — single leaf', () => {
  it('dispatches the leaf; emits start + node_complete + complete; summary.succeeded=1', async () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r', { text: 't' }),
      mkSend('s', 'u', undefined, { state: 'edited' }),
    ])
    const { sink, events } = mkMockSinkPlus()
    const { api, releaseNext } = mkControllableApi()

    const wavePromise = runWave({
      ...STANDARD,
      tree,
      S: new Set([nodeId('s')]),
      sink,
      api,
    })
    await flushMicrotasks()
    releaseNext()
    const summary = await wavePromise

    expect(events().map((e) => e.kind)).toEqual(['start', 'node_complete', 'complete'])
    const nodeCompleted = events().find((e): e is Extract<WaveEvent, { kind: 'node_complete' }> => e.kind === 'node_complete')!
    expect(nodeCompleted.nodeId).toBe(nodeId('s'))
    expect(nodeCompleted.outcome).toBe('success')
    expect(summary).toMatchObject({
      succeeded: 1,
      failed: { transient: 0, rate_limited: 0, permanent: 0 },
      blocked: 0,
      cancelled: 0,
      reflog_evicted: 0,
    })
  })
})

// ============================================================================
// 3. Multi-leaf fan happy path
// ============================================================================

describe('runWave — 3-leaf fan all succeed', () => {
  it('dispatches every leaf; summary.succeeded=3; one node_complete per leaf', async () => {
    const tree = build3LeafFan()
    const { sink, events } = mkMockSinkPlus()
    const { api, releaseNext, pendingCount } = mkControllableApi()

    const wavePromise = runWave({
      ...STANDARD,
      tree,
      S: new Set([nodeId('s_a'), nodeId('s_b'), nodeId('s_c')]),
      sink,
      api,
      maxParallel: 4,
    })
    // All three pick up immediately (maxParallel >= 3).
    await flushMicrotasks()
    expect(pendingCount()).toBe(3)
    releaseNext()
    releaseNext()
    releaseNext()
    const summary = await wavePromise

    expect(events().filter((e) => e.kind === 'node_complete')).toHaveLength(3)
    expect(summary.succeeded).toBe(3)
    expect(summary.failed).toEqual({ transient: 0, rate_limited: 0, permanent: 0 })
  })
})

// ============================================================================
// 4. Concurrency cap enforcement
// ============================================================================

describe('runWave — concurrency cap', () => {
  it('with maxParallel=2 and 5 ready leaves, inflight count never exceeds the cap', async () => {
    const tree = buildNLeafFan(5)
    const { sink } = mkMockSinkPlus()
    const { api, inflight, releaseNext, pendingCount } = mkControllableApi()
    const leaves = Array.from({ length: 5 }, (_, i) => nodeId(`s_${i}`))

    const wavePromise = runWave({
      ...STANDARD,
      tree,
      S: new Set(leaves),
      sink,
      api,
      maxParallel: 2,
    })

    // Drain every leaf by repeatedly waiting for at least one to be pending,
    // then releasing it. Each release lets the loop pick the next ready leaf;
    // the cap is enforced by `inflight.max` which the test asserts at the end.
    for (let i = 0; i < 5; i++) {
      await waitFor(() => pendingCount() >= 1, `at least 1 pending (iteration ${i})`)
      releaseNext()
    }
    const summary = await wavePromise

    expect(summary.succeeded).toBe(5)
    // The actual invariant: inflight max never exceeded the cap throughout the wave.
    expect(inflight.max).toBe(2)
    expect(inflight.max).toBeLessThanOrEqual(2)
  })

  it('defaults maxParallel to 4 when omitted', async () => {
    const tree = buildNLeafFan(6)
    const { sink } = mkMockSinkPlus()
    const { api, inflight, releaseNext } = mkControllableApi()
    const leaves = Array.from({ length: 6 }, (_, i) => nodeId(`s_${i}`))

    const wavePromise = runWave({
      ...STANDARD,
      tree,
      S: new Set(leaves),
      sink,
      api,
      // maxParallel omitted → default 4
    })
    await flushMicrotasks()
    expect(inflight.max).toBe(4)
    for (let i = 0; i < 6; i++) {
      releaseNext()
      await flushMicrotasks()
    }
    await wavePromise
    expect(inflight.max).toBe(4)
  })
})

// ============================================================================
// 5. Wave-event ordering and shape
// ============================================================================

describe('runWave — wave events', () => {
  it("emits 'start' first with the right metadata", async () => {
    const tree = build3LeafFan()
    const { sink, events } = mkMockSinkPlus()
    const { api, releaseNext } = mkControllableApi()

    const wavePromise = runWave({
      ...STANDARD,
      tree,
      S: new Set([nodeId('s_a'), nodeId('s_b'), nodeId('s_c')]),
      sink,
      api,
    })
    await flushMicrotasks()

    const start = events()[0]
    expect(start.kind).toBe('start')
    if (start.kind === 'start') {
      expect(start.waveId).toBe('wave-uuid-1')
      expect(start.triggerKind).toBe('refresh_tree')
      expect(start.treeId).toBe(treeId('t-1'))
      // estimatedCalls is sum across leaves of (1 + freshSuffix.length).
      // Each of the 3 leaves: 1 create + 1 add_message = 2 calls each → 6.
      expect(start.estimatedCalls).toBe(6)
      expect(start.emittedAt).toMatch(/Z$|\+\d{2}:?\d{2}$/)
    }

    for (let i = 0; i < 3; i++) releaseNext()
    await wavePromise
  })

  it('emits one node_complete per dispatched leaf with the correct outcome', async () => {
    const tree = build3LeafFan()
    const { sink, events } = mkMockSinkPlus()
    const { api, releaseNext, failNext } = mkControllableApi()
    const wavePromise = runWave({
      ...STANDARD,
      tree,
      S: new Set([nodeId('s_a'), nodeId('s_b'), nodeId('s_c')]),
      sink,
      api,
    })
    await flushMicrotasks()
    releaseNext()
    await flushMicrotasks()
    failNext(mkApiError({ status: 500 }))
    await flushMicrotasks()
    releaseNext()
    await wavePromise

    const completes = events().filter(
      (e): e is Extract<WaveEvent, { kind: 'node_complete' }> => e.kind === 'node_complete',
    )
    expect(completes).toHaveLength(3)
    const outcomes = completes.map((e) => e.outcome).sort()
    expect(outcomes).toEqual(['failure', 'success', 'success'])
  })

  it("emits 'complete' last with the bucketed summary", async () => {
    const tree = build3LeafFan()
    const { sink, events } = mkMockSinkPlus()
    const { api, releaseNext, failNext } = mkControllableApi()
    const wavePromise = runWave({
      ...STANDARD,
      tree,
      S: new Set([nodeId('s_a'), nodeId('s_b'), nodeId('s_c')]),
      sink,
      api,
    })
    await flushMicrotasks()
    releaseNext()
    await flushMicrotasks()
    failNext(mkApiError({ status: 429, detail: 'rate' }))
    await flushMicrotasks()
    failNext(mkApiError({ status: 400, detail: 'bad operator' }))
    await wavePromise

    const last = events()[events().length - 1]
    expect(last.kind).toBe('complete')
    if (last.kind === 'complete') {
      expect(last.summary).toMatchObject({
        succeeded: 1,
        failed: { transient: 0, rate_limited: 1, permanent: 1 },
        blocked: 0,
        cancelled: 0,
        reflog_evicted: 0,
      })
    }
  })
})

// ============================================================================
// 6. In-flight cascade — failed shared interior Send drops ready siblings to blocked
// ============================================================================

describe('runWave — in-flight cascade', () => {
  it('with maxParallel=1: 3 siblings share a stale interior Send; failure on it drops 2 others to blocked', async () => {
    // r → u → s_shared(stale) → u_fan → fan → s_a / s_b / s_c
    // Each leaf's dispatch includes s_shared. maxParallel=1 → leaves run
    // serially. Leaf #1's first add_message (for s_shared) fails. The
    // cascade fires: s_b and s_c are still in `ready`, get dropped to
    // blocked, no further dispatches fire.
    const tree = buildSharedInteriorFanTree()
    const { sink, callsOf } = mkMockSinkPlus()
    const { api, failNext, createCalls, addMessageCalls } = mkControllableApi()

    const wavePromise = runWave({
      ...STANDARD,
      tree,
      S: new Set([nodeId('s_shared'), nodeId('s_a'), nodeId('s_b'), nodeId('s_c')]),
      sink,
      api,
      maxParallel: 1,
    })

    // First leaf picks up: 1 create + 1 add_message in flight (for s_shared).
    await flushMicrotasks()
    expect(createCalls).toHaveLength(1)
    expect(addMessageCalls).toHaveLength(1)
    // Fail s_shared: cascade should fire.
    failNext(mkApiError({ status: 500, detail: 'boom' }))
    const summary = await wavePromise

    // Only the first leaf actually dispatched; no further create_attack fired.
    expect(createCalls).toHaveLength(1)
    expect(addMessageCalls).toHaveLength(1)

    // Summary: 1 failed.transient (the first leaf), 2 blocked.
    expect(summary).toMatchObject({
      succeeded: 0,
      failed: { transient: 1, rate_limited: 0, permanent: 0 },
      blocked: 2,
      cancelled: 0,
    })

    // The two blocked siblings transitioned to `stale` with failure_class='blocked'.
    for (const id of ['s_b', 's_c']) {
      const stateTransitions = callsOf('setNodeState').filter((c) => c.nodeId === nodeId(id))
      // Last state should be 'stale' with a blocked reason.
      const blockedTransition = stateTransitions.find(
        (c) => c.state === 'stale' && typeof c.reason === 'object' && c.reason !== null && 'failure_class' in c.reason,
      )
      expect(blockedTransition).toBeDefined()
      expect(blockedTransition?.reason).toMatchObject({ failure_class: 'blocked' })
    }
  })

  it('cascade only blocks NOT-YET-DISPATCHED siblings (in-flight ones complete independently)', async () => {
    // Same tree; maxParallel=3 so all 3 leaves dispatch in parallel.
    // The first leaf's dispatch fails on s_shared. The OTHER two leaves
    // are already in flight when that happens — they should NOT be
    // dropped to blocked. They each independently complete (and may
    // independently fail on s_shared too, but their outcomes are their
    // own per-leaf failures, not cascade-blocked).
    const tree = buildSharedInteriorFanTree()
    const { sink } = mkMockSinkPlus()
    const { api, failNext, addMessageCalls } = mkControllableApi()

    const wavePromise = runWave({
      ...STANDARD,
      tree,
      S: new Set([nodeId('s_shared'), nodeId('s_a'), nodeId('s_b'), nodeId('s_c')]),
      sink,
      api,
      maxParallel: 3,
    })
    await flushMicrotasks()
    // All 3 leaves have started: 3 create_attacks + 3 add_messages for s_shared.
    expect(addMessageCalls).toHaveLength(3)
    // Each of them fails on s_shared.
    failNext(mkApiError({ status: 500 }))
    await flushMicrotasks()
    failNext(mkApiError({ status: 500 }))
    await flushMicrotasks()
    failNext(mkApiError({ status: 500 }))
    const summary = await wavePromise

    // All three leaves dispatched and independently failed. Zero blocked.
    expect(summary).toMatchObject({
      succeeded: 0,
      failed: { transient: 3, rate_limited: 0, permanent: 0 },
      blocked: 0,
    })
  })

  it('cascade with a mix of blocked-by-cascade and clean siblings', async () => {
    // Two fan groups sharing different paths:
    //   r → u → s_shared(stale) → u_fan_A → fan_A → s_a1 / s_a2
    //                          → u_fan_B → fan_B → s_b1
    // s_shared is shared by ALL leaves. maxParallel=1.
    // Leaf order: s_a1, s_a2, s_b1 (per insertion).
    // s_a1 dispatches first; its add_message for s_shared fails.
    // Cascade should drop s_a2 AND s_b1 (both have s_shared in path).
    // Summary: 1 failed + 2 blocked.
    const nodes: ConversationTreeNode[] = [
      mkRoot('r'),
      mkUserTurn('u', 'r', { text: 'shared' }),
      mkSend('s_shared', 'u', undefined, { state: 'stale' }),
      mkUserTurn('u_fan_A', 's_shared', { text: 'A' }),
      mkFan('fan_A', 'u_fan_A', {
        axis: 'attempt',
        variants: [{ axis: 'attempt', payload: {} }, { axis: 'attempt', payload: {} }],
      }),
      mkSend('s_a1', 'fan_A', undefined, { state: 'edited' }),
      mkSend('s_a2', 'fan_A', undefined, { state: 'edited' }),
      mkUserTurn('u_fan_B', 's_shared', { text: 'B' }),
      mkFan('fan_B', 'u_fan_B', { axis: 'attempt', variants: [{ axis: 'attempt', payload: {} }] }),
      mkSend('s_b1', 'fan_B', undefined, { state: 'edited' }),
    ]
    const tree = mkTree('r', nodes)
    const { sink } = mkMockSinkPlus()
    const { api, failNext } = mkControllableApi()

    const wavePromise = runWave({
      ...STANDARD,
      tree,
      S: new Set([nodeId('s_shared'), nodeId('s_a1'), nodeId('s_a2'), nodeId('s_b1')]),
      sink,
      api,
      maxParallel: 1,
    })
    await flushMicrotasks()
    failNext(mkApiError({ status: 500, detail: 'shared boom' }))
    const summary = await wavePromise

    expect(summary.failed.transient).toBe(1)
    expect(summary.blocked).toBe(2)
    expect(summary.succeeded).toBe(0)
  })
})

// ============================================================================
// 7. Cancellation via WaveDispatchController
// ============================================================================

describe('runWave — cancellation', () => {
  it('cancel before any dispatch: all leaves transition to cancelled; no API calls fire', async () => {
    const tree = build3LeafFan()
    const { sink } = mkMockSinkPlus()
    const { api, createCalls } = mkControllableApi()
    const controller = createWaveController()
    controller.cancel()

    const summary = await runWave({
      ...STANDARD,
      tree,
      S: new Set([nodeId('s_a'), nodeId('s_b'), nodeId('s_c')]),
      sink,
      api,
      controller,
    })

    expect(createCalls).toHaveLength(0)
    expect(summary).toMatchObject({ succeeded: 0, cancelled: 3 })
  })

  it('cancel mid-wave: in-flight completes; not-yet-dispatched leaves → cancelled', async () => {
    // 5 leaves, maxParallel=1. After leaf 1 completes successfully, cancel.
    // Leaves 2..5 should all transition to cancelled.
    const tree = buildNLeafFan(5)
    const { sink } = mkMockSinkPlus()
    const { api, releaseNext, addMessageCalls, pendingCount } = mkControllableApi()
    const controller = createWaveController()

    const wavePromise = runWave({
      ...STANDARD,
      tree,
      S: new Set(Array.from({ length: 5 }, (_, i) => nodeId(`s_${i}`))),
      sink,
      api,
      maxParallel: 1,
      controller,
    })

    // Leaf 1 reaches its addMessage await.
    await waitFor(() => pendingCount() === 1, 'leaf 0 add_message pending')
    releaseNext()
    // Leaf 2 reaches its addMessage await.
    await waitFor(() => pendingCount() === 1 && addMessageCalls.length === 2, 'leaf 1 picked up')

    // Cancel; let leaf 2 finish gracefully.
    controller.cancel()
    releaseNext()
    const summary = await wavePromise

    // 2 succeeded (the in-flight ones); 3 cancelled.
    expect(summary.succeeded).toBe(2)
    expect(summary.cancelled).toBe(3)
    // No further API calls beyond the 2 that were in flight.
    expect(addMessageCalls).toHaveLength(2)
  })

  it('execution-clobber gate: in-flight leaves that finish AFTER cancel still record their execution', async () => {
    // Two leaves, maxParallel=2. Both start. Cancel fires. Leaf 1 then
    // succeeds; leaf 2 then succeeds. Both should have their executions
    // recorded (not clobbered with `cancelled` state). The summary tallies
    // them as `succeeded`, not `cancelled`.
    const tree = buildNLeafFan(2)
    const { sink, callsOf } = mkMockSinkPlus()
    const { api, releaseNext } = mkControllableApi()
    const controller = createWaveController()

    const wavePromise = runWave({
      ...STANDARD,
      tree,
      S: new Set([nodeId('s_0'), nodeId('s_1')]),
      sink,
      api,
      maxParallel: 2,
      controller,
    })
    await flushMicrotasks()
    controller.cancel()
    releaseNext()
    await flushMicrotasks()
    releaseNext()
    const summary = await wavePromise

    expect(summary.succeeded).toBe(2)
    expect(summary.cancelled).toBe(0)
    // Both leaves got recordExecution calls.
    expect(callsOf('recordExecution').filter((c) => c.nodeId === nodeId('s_0'))).toHaveLength(1)
    expect(callsOf('recordExecution').filter((c) => c.nodeId === nodeId('s_1'))).toHaveLength(1)
  })

  it('controller defaults to never-cancelled when omitted', async () => {
    const tree = build3LeafFan()
    const { sink } = mkMockSinkPlus()
    const { api, releaseNext } = mkControllableApi()
    const wavePromise = runWave({
      ...STANDARD,
      tree,
      S: new Set([nodeId('s_a'), nodeId('s_b'), nodeId('s_c')]),
      sink,
      api,
    })
    await flushMicrotasks()
    for (let i = 0; i < 3; i++) releaseNext()
    const summary = await wavePromise
    expect(summary.succeeded).toBe(3)
  })
})

// ============================================================================
// 8. Summary failure-class bucketing across mixed outcomes
// ============================================================================

describe('runWave — summary bucketing', () => {
  it('mixed failure classes across leaves bucket correctly', async () => {
    const tree = buildNLeafFan(4)
    const { sink } = mkMockSinkPlus()
    const { api, releaseNext, failNext } = mkControllableApi()

    const wavePromise = runWave({
      ...STANDARD,
      tree,
      S: new Set([nodeId('s_0'), nodeId('s_1'), nodeId('s_2'), nodeId('s_3')]),
      sink,
      api,
      maxParallel: 4,
    })
    await flushMicrotasks()
    releaseNext() // s_0 succeeds
    failNext(mkApiError({ status: 500 })) // s_1 transient
    failNext(mkApiError({ status: 429 })) // s_2 rate_limited
    failNext(mkApiError({ status: 400, detail: 'bad operator' })) // s_3 permanent
    const summary = await wavePromise

    expect(summary).toMatchObject({
      succeeded: 1,
      failed: { transient: 1, rate_limited: 1, permanent: 1 },
      blocked: 0,
      cancelled: 0,
    })
  })
})

// ============================================================================
// Helpers
// ============================================================================

function build3LeafFan() {
  return mkTree('r', [
    mkRoot('r'),
    mkUserTurn('u', 'r', { text: 'shared' }),
    mkFan('f', 'u', {
      axis: 'attempt',
      variants: [
        { axis: 'attempt', payload: {} },
        { axis: 'attempt', payload: {} },
        { axis: 'attempt', payload: {} },
      ],
    }),
    mkSend('s_a', 'f', undefined, { state: 'edited' }),
    mkSend('s_b', 'f', undefined, { state: 'edited' }),
    mkSend('s_c', 'f', undefined, { state: 'edited' }),
  ])
}

function buildNLeafFan(n: number) {
  const nodes: ConversationTreeNode[] = [
    mkRoot('r'),
    mkUserTurn('u', 'r', { text: 'shared' }),
    mkFan('f', 'u', {
      axis: 'attempt',
      variants: Array.from({ length: n }, () => ({ axis: 'attempt' as const, payload: {} })),
    }),
  ]
  for (let i = 0; i < n; i++) {
    nodes.push(mkSend(`s_${i}`, 'f', undefined, { state: 'edited' }))
  }
  return mkTree('r', nodes)
}

function buildSharedInteriorFanTree() {
  return mkTree('r', [
    mkRoot('r'),
    mkUserTurn('u', 'r', { text: 'shared' }),
    mkSend('s_shared', 'u', undefined, { state: 'stale' }),
    mkUserTurn('u_fan', 's_shared', { text: 'per-fan' }),
    mkFan('f', 'u_fan', {
      axis: 'attempt',
      variants: [
        { axis: 'attempt', payload: {} },
        { axis: 'attempt', payload: {} },
        { axis: 'attempt', payload: {} },
      ],
    }),
    mkSend('s_a', 'f', undefined, { state: 'edited' }),
    mkSend('s_b', 'f', undefined, { state: 'edited' }),
    mkSend('s_c', 'f', undefined, { state: 'edited' }),
  ])
}

function emptySummary(): WaveSummary {
  return {
    succeeded: 0,
    failed: { transient: 0, rate_limited: 0, permanent: 0 },
    blocked: 0,
    cancelled: 0,
    reflog_evicted: 0,
  }
}

/**
 * Thin extension over mkMockSink that exposes `events()` for the wave-event
 * subset of calls. We removed the equivalent helper from mkMockSink itself
 * during PR4a.1's review-driven trim; runWave tests need it back locally
 * because they're event-centric.
 */
function mkMockSinkPlus() {
  const base = mkMockSink()
  return {
    ...base,
    events: () => base.callsOf('emitWaveEvent').map((c) => c.event),
  }
}

// Re-export a NodeState alias to keep type imports terse below.
export type { NodeState }
