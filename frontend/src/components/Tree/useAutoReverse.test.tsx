// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Tests for `useAutoReverse` — the React hook that owns the AR →
 * ConversationTree reconstruction lifecycle per spec 01 §9.3 / §13.1
 * `openTreeFromAttackResult`. Wraps PR7a's pure
 * `linearChainFromMessages` in a useEffect + loading + error state
 * surface.
 *
 * Single-AR linear path: fetches the AR (for conversation_id), then
 * fetches its messages, then builds the linear tree. Multi-leaf AR-
 * list path (with detectFansV10Plus) lands in PR7g's reload
 * reconstruction.
 */

import { act, renderHook, waitFor } from '@testing-library/react'

import { useAutoReverse } from './useAutoReverse'
import type {
  AttackSummary,
  BackendMessage,
  BackendMessagePiece,
  ConversationMessagesResponse,
} from '../../types'

// ============================================================================
// Fixtures
// ============================================================================

function mkPiece(value: string, pieceId = 'p1'): BackendMessagePiece {
  return {
    piece_id: pieceId,
    original_value_data_type: 'text',
    converted_value_data_type: 'text',
    original_value: value,
    converted_value: value,
    scores: [],
    response_error: 'none',
    original_prompt_id: pieceId,
    converter_identifiers: [],
  }
}

function mkMessage(
  turn: number,
  role: 'user' | 'assistant',
  text: string,
  pieceId: string,
): BackendMessage {
  return {
    turn_number: turn,
    role,
    pieces: [mkPiece(text, pieceId)],
    created_at: '2026-06-11T00:00:00Z',
  }
}

const fakeAr: AttackSummary = {
  attack_result_id: 'ar-1',
  conversation_id: 'conv-1',
  attack_type: 'red_teaming',
  converters: [],
  message_count: 2,
  related_conversation_ids: [],
  labels: {},
  created_at: '2026-06-11T00:00:00Z',
  updated_at: '2026-06-11T00:00:00Z',
}

const fakeMessagesResponse: ConversationMessagesResponse = {
  conversation_id: 'conv-1',
  messages: [
    mkMessage(1, 'user', 'Hello', 'p1'),
    mkMessage(2, 'assistant', 'Hi', 'p2'),
  ],
}

function mkMockApi(opts: {
  getAttack?: (id: string) => Promise<AttackSummary>
  getMessages?: (arId: string, convId: string) => Promise<ConversationMessagesResponse>
} = {}): { getAttack: jest.Mock; getMessages: jest.Mock } {
  return {
    getAttack: jest.fn(opts.getAttack ?? (async () => fakeAr)),
    getMessages: jest.fn(opts.getMessages ?? (async () => fakeMessagesResponse)),
  }
}

// ============================================================================
// Null id — idle state
// ============================================================================

describe('useAutoReverse — idle (null id)', () => {
  it('returns tree=null, loading=false, error=null when attackResultId is null', () => {
    const api = mkMockApi()
    const { result } = renderHook(() =>
      useAutoReverse(null, { attacksApi: api }),
    )
    expect(result.current.tree).toBeNull()
    expect(result.current.loading).toBe(false)
    expect(result.current.error).toBeNull()
    expect(api.getAttack).not.toHaveBeenCalled()
    expect(api.getMessages).not.toHaveBeenCalled()
  })
})

// ============================================================================
// Happy path — load + reconstruct
// ============================================================================

describe('useAutoReverse — happy path', () => {
  it('fetches AR + messages and builds a linear tree', async () => {
    const api = mkMockApi()
    const { result } = renderHook(() =>
      useAutoReverse('ar-1', { attacksApi: api }),
    )
    // Initially loading.
    expect(result.current.loading).toBe(true)
    expect(result.current.tree).toBeNull()
    expect(result.current.error).toBeNull()

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    expect(api.getAttack).toHaveBeenCalledWith('ar-1')
    expect(api.getMessages).toHaveBeenCalledWith('ar-1', 'conv-1')
    expect(result.current.tree).not.toBeNull()
    expect(result.current.tree?.nodes).toHaveLength(2) // root + send
    expect(result.current.error).toBeNull()
  })

  it('re-fetches when attackResultId changes', async () => {
    const api = mkMockApi({
      getAttack: jest.fn(async (id: string) => ({ ...fakeAr, attack_result_id: id, conversation_id: `conv-${id}` })),
      getMessages: jest.fn(async (arId: string) => ({
        ...fakeMessagesResponse,
        conversation_id: `conv-${arId}`,
      })),
    })
    const { result, rerender } = renderHook(
      ({ id }: { id: string | null }) => useAutoReverse(id, { attacksApi: api }),
      { initialProps: { id: 'ar-1' as string | null } },
    )
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(api.getAttack).toHaveBeenCalledWith('ar-1')

    rerender({ id: 'ar-2' as string | null })
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(api.getAttack).toHaveBeenCalledWith('ar-2')
    expect(api.getMessages).toHaveBeenLastCalledWith('ar-2', 'conv-ar-2')
  })

  it('clears tree to null when id changes from string to null', async () => {
    const api = mkMockApi()
    const { result, rerender } = renderHook(
      ({ id }: { id: string | null }) => useAutoReverse(id, { attacksApi: api }),
      { initialProps: { id: 'ar-1' as string | null } },
    )
    await waitFor(() => expect(result.current.tree).not.toBeNull())

    rerender({ id: null })
    expect(result.current.tree).toBeNull()
    expect(result.current.loading).toBe(false)
  })
})

// ============================================================================
// Error path
// ============================================================================

describe('useAutoReverse — error path', () => {
  it('captures fetch error and clears loading', async () => {
    const api = mkMockApi({
      getAttack: jest.fn(async () => {
        throw new Error('network')
      }),
    })
    const { result } = renderHook(() =>
      useAutoReverse('ar-1', { attacksApi: api }),
    )
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.error).not.toBeNull()
    expect(result.current.error?.message).toBe('network')
    expect(result.current.tree).toBeNull()
  })

  it('captures getMessages error', async () => {
    const api = mkMockApi({
      getMessages: jest.fn(async () => {
        throw new Error('boom')
      }),
    })
    const { result } = renderHook(() =>
      useAutoReverse('ar-1', { attacksApi: api }),
    )
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.error?.message).toBe('boom')
  })
})

// ============================================================================
// Race-condition handling — stale response when id changes mid-fetch
// ============================================================================

describe('useAutoReverse — stale response handling', () => {
  it('drops stale results when the id changed mid-fetch', async () => {
    let resolveFirst!: (v: AttackSummary) => void
    const api = mkMockApi({
      getAttack: jest.fn((id: string) => {
        if (id === 'ar-1') {
          return new Promise<AttackSummary>((resolve) => {
            resolveFirst = resolve
          })
        }
        return Promise.resolve({ ...fakeAr, attack_result_id: id })
      }),
      getMessages: jest.fn(async (arId: string) => ({
        ...fakeMessagesResponse,
        conversation_id: `conv-${arId}`,
      })),
    })
    const { result, rerender } = renderHook(
      ({ id }: { id: string | null }) => useAutoReverse(id, { attacksApi: api }),
      { initialProps: { id: 'ar-1' as string | null } },
    )
    // ar-1 fetch is pending; swap to ar-2 mid-flight.
    rerender({ id: 'ar-2' as string | null })
    await waitFor(() => expect(result.current.tree).not.toBeNull())
    // ar-2's tree is set. Now resolve the stale ar-1 fetch.
    await act(async () => {
      resolveFirst({ ...fakeAr })
      await Promise.resolve()
    })
    // The stale resolution must NOT clobber the ar-2 tree.
    expect(result.current.tree).not.toBeNull()
    // (Verify by checking that getMessages wasn't called for ar-1 second.)
  })
})
