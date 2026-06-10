// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Tests for the pure dispatch helpers `buildLabels` and `formatApiError`.
 *
 * `buildLabels` produces the `Record<string, string>` that goes on every
 * `create_attack` and `add_message` request in a leaf's dispatch sequence.
 * The labels-divergence invariant (every call in the sequence carries an
 * identical labels dict) is enforced at the dispatcher layer, but tested
 * here at the source: `buildLabels` is called once per dispatch and reused.
 *
 * `formatApiError` classifies an error into one of four failure classes
 * (transient / rate_limited / permanent / blocked) so the wave-summary
 * toast and [Retry failed] gating can drive distinct UX per class.
 */

import type { ApiError } from '../services/errors'
import { buildLabels, formatApiError, isTreePathLabelValid, parseTreePathLabel } from './dispatchHelpers'
import { treeId } from './testHelpers'

// ============================================================================
// buildLabels
// ============================================================================

describe('buildLabels', () => {
  it('emits the V1.0 required labels with stringified values', () => {
    const labels = buildLabels({
      operator: 'alice',
      operation: 'red-team-1',
      treeId: treeId('t-1'),
      waveId: 'wave-uuid-1',
      waveTriggerKind: 'refresh_tree',
      treePathSegments: [],
      parentConversationTreeId: null,
    })
    expect(labels).toEqual({
      operator: 'alice',
      operation: 'red-team-1',
      conversation_tree_id: 't-1',
      wave_id: 'wave-uuid-1',
      wave_trigger_kind: 'refresh_tree',
      tree_path: '[]',
    })
  })

  it('JSON-encodes treePathSegments as a single label value', () => {
    const labels = buildLabels({
      operator: 'alice',
      operation: '',
      treeId: treeId('t-1'),
      waveId: 'w-1',
      waveTriggerKind: 'refresh_node',
      treePathSegments: [
        ['prompt', 2],
        ['attempt', 3],
      ],
      parentConversationTreeId: null,
    })
    expect(labels.tree_path).toBe('[["prompt",2],["attempt",3]]')
  })

  it('emits operation as empty string when not provided (matches existing chat behavior)', () => {
    const labels = buildLabels({
      operator: 'alice',
      operation: '',
      treeId: treeId('t-1'),
      waveId: 'w-1',
      waveTriggerKind: 'refresh_node',
      treePathSegments: [],
      parentConversationTreeId: null,
    })
    expect(labels.operation).toBe('')
  })

  it('OMITS parent_conversation_tree_id when null (does not write empty string)', () => {
    // Writing parent_conversation_tree_id='' would surface a self-parent
    // row in History "Open clones of T" — actively wrong. Omission is the
    // honest signal: "this tree has no parent".
    const labels = buildLabels({
      operator: 'alice',
      operation: '',
      treeId: treeId('t-1'),
      waveId: 'w-1',
      waveTriggerKind: 'refresh_tree',
      treePathSegments: [],
      parentConversationTreeId: null,
    })
    expect(labels).not.toHaveProperty('parent_conversation_tree_id')
  })

  it('emits parent_conversation_tree_id when set (cloned tree)', () => {
    const labels = buildLabels({
      operator: 'alice',
      operation: '',
      treeId: treeId('t-clone'),
      waveId: 'w-1',
      waveTriggerKind: 'refresh_tree',
      treePathSegments: [],
      parentConversationTreeId: treeId('t-source'),
    })
    expect(labels.parent_conversation_tree_id).toBe('t-source')
  })

  it('throws on empty operator (the tag-hygiene gate is the load-bearing check)', () => {
    // The runner's entry-point shim rejects empty-operator waves at step 1,
    // so this should never fire in production. The assert makes the gap
    // loud if a future refactor bypasses the gate.
    expect(() =>
      buildLabels({
        operator: '',
        operation: '',
        treeId: treeId('t-1'),
        waveId: 'w-1',
        waveTriggerKind: 'refresh_node',
        treePathSegments: [],
        parentConversationTreeId: null,
      }),
    ).toThrow(/operator.*required/i)
  })

  it('throws on null/undefined operator (defensive)', () => {
    expect(() =>
      buildLabels({
        // @ts-expect-error — testing the runtime guard
        operator: null,
        operation: '',
        treeId: treeId('t-1'),
        waveId: 'w-1',
        waveTriggerKind: 'refresh_node',
        treePathSegments: [],
        parentConversationTreeId: null,
      }),
    ).toThrow(/operator.*required/i)
  })

  // ----------------------------------------------------------------------
  // Labels-divergence invariant: every call in a leaf's dispatch must
  // carry identical labels. Validated here at the source by passing the
  // same input twice and asserting deep equality. The dispatcher (PR4c2)
  // tests the call-site invariant against a mock API client.
  // ----------------------------------------------------------------------

  it('two calls with identical inputs produce deep-equal labels (call-site invariant source)', () => {
    const args = {
      operator: 'alice',
      operation: 'op-1',
      treeId: treeId('t-1'),
      waveId: 'w-1',
      waveTriggerKind: 'refresh_subtree' as const,
      treePathSegments: [['converter', 0], ['attempt', 2]] as Array<[string, number]>,
      parentConversationTreeId: treeId('t-parent'),
    }
    expect(buildLabels(args)).toEqual(buildLabels(args))
  })
})

// ============================================================================
// tree_path JSON encoding round-trip
// ============================================================================

describe('tree_path encoding', () => {
  it('round-trips through parseTreePathLabel', () => {
    const segments: Array<[string, number]> = [
      ['prompt', 0],
      ['attempt', 7],
    ]
    const label = buildLabels({
      operator: 'a',
      operation: '',
      treeId: treeId('t'),
      waveId: 'w',
      waveTriggerKind: 'refresh_tree',
      treePathSegments: segments,
      parentConversationTreeId: null,
    }).tree_path
    expect(parseTreePathLabel(label)).toEqual(segments)
  })

  it('produces "[]" (not absent) for fan-less leaves', () => {
    const label = buildLabels({
      operator: 'a',
      operation: '',
      treeId: treeId('t'),
      waveId: 'w',
      waveTriggerKind: 'refresh_node',
      treePathSegments: [],
      parentConversationTreeId: null,
    }).tree_path
    expect(label).toBe('[]')
    expect(parseTreePathLabel(label)).toEqual([])
  })

  it('parseTreePathLabel returns [] for absent / empty / malformed input (fail-soft)', () => {
    expect(parseTreePathLabel(undefined)).toEqual([])
    expect(parseTreePathLabel('')).toEqual([])
    expect(parseTreePathLabel('not json')).toEqual([])
    expect(parseTreePathLabel('{"not":"array"}')).toEqual([])
    expect(parseTreePathLabel('[[1, "string-instead-of-number"]]')).toEqual([])
  })

  it('isTreePathLabelValid distinguishes valid empty from malformed', () => {
    expect(isTreePathLabelValid('[]')).toBe(true)
    expect(isTreePathLabelValid('[["axis", 0]]')).toBe(true)
    expect(isTreePathLabelValid('not json')).toBe(false)
    expect(isTreePathLabelValid('[[1, 1]]')).toBe(false) // axis must be string
  })
})

// ============================================================================
// formatApiError — failure-class classification
// ============================================================================

describe('formatApiError', () => {
  const err = (overrides: Partial<ApiError>): ApiError => ({
    status: 500,
    detail: 'server boom',
    isNetworkError: false,
    isTimeout: false,
    raw: null,
    ...overrides,
  })

  // ----- transient (retry-eligible automatically) -----

  it('classifies network errors as transient', () => {
    const r = formatApiError(err({ status: null, isNetworkError: true, detail: 'ECONNRESET' }), 'add_message')
    expect(r.failure_class).toBe('transient')
    expect(r.message).toMatch(/add_message/)
  })

  it('classifies timeouts as transient', () => {
    const r = formatApiError(err({ status: null, isTimeout: true, detail: 'timeout' }), 'create_attack')
    expect(r.failure_class).toBe('transient')
  })

  it('classifies 5xx as transient', () => {
    expect(formatApiError(err({ status: 500 }), 'create_attack').failure_class).toBe('transient')
    expect(formatApiError(err({ status: 502 }), 'create_attack').failure_class).toBe('transient')
    expect(formatApiError(err({ status: 503 }), 'add_message').failure_class).toBe('transient')
    expect(formatApiError(err({ status: 504 }), 'add_message').failure_class).toBe('transient')
  })

  // ----- rate_limited -----

  it('classifies HTTP 429 as rate_limited', () => {
    const r = formatApiError(err({ status: 429, detail: 'rate limit' }), 'add_message')
    expect(r.failure_class).toBe('rate_limited')
  })

  it('classifies provider-specific 529 (Anthropic overloaded) as rate_limited', () => {
    const r = formatApiError(err({ status: 529, detail: 'overloaded_error' }), 'add_message')
    expect(r.failure_class).toBe('rate_limited')
  })

  it('classifies error bodies mentioning "rate_limit_exceeded" as rate_limited (provider-agnostic shape)', () => {
    const r = formatApiError(err({ status: 400, detail: 'rate_limit_exceeded: try again' }), 'add_message')
    expect(r.failure_class).toBe('rate_limited')
  })

  it('classifies error bodies mentioning "overloaded_error" as rate_limited', () => {
    const r = formatApiError(err({ status: 500, detail: 'overloaded_error from upstream' }), 'add_message')
    expect(r.failure_class).toBe('rate_limited')
  })

  // ----- permanent -----

  it('classifies generic 4xx as permanent', () => {
    expect(formatApiError(err({ status: 400, detail: 'bad request' }), 'create_attack').failure_class).toBe(
      'permanent',
    )
    expect(formatApiError(err({ status: 404, detail: 'not found' }), 'create_attack').failure_class).toBe(
      'permanent',
    )
  })

  it('classifies operator-lock 400 as permanent with a recovery-pointer message', () => {
    const r = formatApiError(
      err({ status: 400, detail: "Operator mismatch: attack belongs to operator 'alice' but request is from 'bob'." }),
      'add_message',
    )
    expect(r.failure_class).toBe('permanent')
    expect(r.message).toMatch(/operator|branch/i)
  })

  it('classifies 403 / 401 as permanent', () => {
    expect(formatApiError(err({ status: 401 }), 'add_message').failure_class).toBe('permanent')
    expect(formatApiError(err({ status: 403 }), 'add_message').failure_class).toBe('permanent')
  })

  it('classifies unknown / null status (non-network, non-timeout) as transient (safe default)', () => {
    // Conservative: an unclassifiable error is more likely to be transient
    // than permanent (because permanent requires operator fix; misclassifying
    // permanent as transient just gives the operator an unhelpful Retry that
    // re-fails harmlessly).
    const r = formatApiError(err({ status: null, isNetworkError: false, isTimeout: false }), 'add_message')
    expect(r.failure_class).toBe('transient')
  })

  // ----- message shape -----

  it("prefixes the call name (so leaf.lastError.message identifies which call failed)", () => {
    expect(formatApiError(err({ status: 500 }), 'create_attack').message).toMatch(/create_attack/)
    expect(formatApiError(err({ status: 500 }), 'add_message').message).toMatch(/add_message/)
  })

  it('includes the status code when present', () => {
    expect(formatApiError(err({ status: 429 }), 'add_message').message).toMatch(/429/)
  })

  it('includes the upstream detail string in the message', () => {
    const r = formatApiError(err({ status: 500, detail: 'AzureOpenAI: deployment not found' }), 'add_message')
    expect(r.message).toMatch(/deployment not found/i)
  })
})
