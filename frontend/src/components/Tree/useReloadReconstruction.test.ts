// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { renderHook, waitFor } from '@testing-library/react'

import { useReloadReconstruction } from './useReloadReconstruction'
import type { AttackListResponse, AttackSummary, ConversationMessagesResponse } from '../../types'
import { mkRoot, mkSend, mkTree } from '../../runner/testHelpers'
import type { ConversationTree } from '../../runner/treeTypes'

function mkAttack(overrides: Partial<AttackSummary> = {}): AttackSummary {
  return {
    attack_result_id: 'ar-1',
    conversation_id: 'conv-1',
    attack_type: 'red_teaming',
    converters: [],
    message_count: 2,
    related_conversation_ids: [],
    labels: {},
    created_at: '2026-06-11T00:00:00Z',
    updated_at: '2026-06-11T00:00:00Z',
    ...overrides,
  }
}

function mkList(items: AttackSummary[]): AttackListResponse {
  return {
    items,
    pagination: { has_more: false, next_cursor: null, prev_cursor: null, limit: items.length },
  }
}

function mkMessages(conv = 'conv-1'): ConversationMessagesResponse {
  return {
    conversation_id: conv,
    messages: [
      {
        turn_number: 1,
        role: 'user',
        pieces: [{
          piece_id: 'p1',
          original_value_data_type: 'text',
          converted_value_data_type: 'text',
          original_value: 'hello',
          converted_value: 'hello',
          scores: [],
          response_error: 'none',
          original_prompt_id: 'p1',
          converter_identifiers: [],
        }],
        created_at: '2026-06-11T00:00:00Z',
      },
      {
        turn_number: 2,
        role: 'assistant',
        pieces: [{
          piece_id: 'p2',
          original_value_data_type: 'text',
          converted_value_data_type: 'text',
          original_value: 'hi',
          converted_value: 'hi',
          scores: [],
          response_error: 'none',
          original_prompt_id: 'p2',
          converter_identifiers: [],
        }],
        created_at: '2026-06-11T00:00:00Z',
      },
    ],
  }
}

describe('useReloadReconstruction', () => {
  it('no-op when fragment tree id is null', async () => {
    const onTreeChange = jest.fn()
    const api = {
      listAttacks: jest.fn(async () => mkList([])),
      getMessages: jest.fn(async () => mkMessages()),
    }
    renderHook(() =>
      useReloadReconstruction({
        fragmentTreeId: null,
        currentTree: null,
        onTreeChange,
        reloadApi: api,
      }),
    )
    await new Promise((r) => setTimeout(r, 0))
    expect(api.listAttacks).not.toHaveBeenCalled()
    expect(onTreeChange).not.toHaveBeenCalled()
  })

  it('fetches AR list by conversation_tree_id and reconstructs a tree', async () => {
    const onTreeChange = jest.fn()
    const api = {
      listAttacks: jest.fn(async () =>
        mkList([mkAttack({ labels: { conversation_tree_id: 't-frag' } })]),
      ),
      getMessages: jest.fn(async () => mkMessages('conv-1')),
    }

    renderHook(() =>
      useReloadReconstruction({
        fragmentTreeId: 't-frag',
        currentTree: null,
        onTreeChange,
        reloadApi: api,
      }),
    )

    await waitFor(() => expect(onTreeChange).toHaveBeenCalled())
    expect(api.listAttacks).toHaveBeenCalledWith({
      limit: 200,
      label: ['conversation_tree_id:t-frag'],
    })
    expect(api.getMessages).toHaveBeenCalledWith('ar-1', 'conv-1')

    const tree = onTreeChange.mock.calls[0][0] as ConversationTree
    expect(tree.id).toBe('t-frag')
    expect(tree.nodes.length).toBeGreaterThan(0)
  })

  it('hoists labels.parent_conversation_tree_id into tree.parentConversationTreeId', async () => {
    const onTreeChange = jest.fn()
    const api = {
      listAttacks: jest.fn(async () =>
        mkList([
          mkAttack({
            attack_result_id: 'ar-a',
            labels: {
              conversation_tree_id: 't-frag',
              parent_conversation_tree_id: 't-parent',
            },
          }),
        ]),
      ),
      getMessages: jest.fn(async () => mkMessages('conv-1')),
    }

    renderHook(() =>
      useReloadReconstruction({
        fragmentTreeId: 't-frag',
        currentTree: null,
        onTreeChange,
        reloadApi: api,
      }),
    )

    await waitFor(() => expect(onTreeChange).toHaveBeenCalled())
    const tree = onTreeChange.mock.calls[0][0] as ConversationTree
    expect(tree.parentConversationTreeId).toBe('t-parent')
  })

  it('no-op when currentTree already has the fragment id', async () => {
    const onTreeChange = jest.fn()
    const api = {
      listAttacks: jest.fn(async () => mkList([])),
      getMessages: jest.fn(async () => mkMessages()),
    }
    const currentTree = mkTree('root', [mkRoot('root'), mkSend('send-1', 'root')], { id: 'same-id' })

    renderHook(() =>
      useReloadReconstruction({
        fragmentTreeId: 'same-id',
        currentTree,
        onTreeChange,
        reloadApi: api,
      }),
    )

    await new Promise((r) => setTimeout(r, 0))
    expect(api.listAttacks).not.toHaveBeenCalled()
    expect(onTreeChange).not.toHaveBeenCalled()
  })

  it('no-op when AR list is empty', async () => {
    const onTreeChange = jest.fn()
    const api = {
      listAttacks: jest.fn(async () => mkList([])),
      getMessages: jest.fn(async () => mkMessages()),
    }

    renderHook(() =>
      useReloadReconstruction({
        fragmentTreeId: 'empty-id',
        currentTree: null,
        onTreeChange,
        reloadApi: api,
      }),
    )

    await waitFor(() => expect(api.listAttacks).toHaveBeenCalled())
    expect(onTreeChange).not.toHaveBeenCalled()
  })

  it('discloses degraded reconstruction when the AR set has fan topology', async () => {
    // Two leaves under a fan (tree_path with an attempt axis at distinct
    // slots) — detectFansV10Plus would find a fan, but slice-1 reload only
    // reconstructs the linear base. The loss must be DISCLOSED, not silent.
    const onTreeChange = jest.fn()
    const onReconstructionDegraded = jest.fn()
    const api = {
      listAttacks: jest.fn(async () =>
        mkList([
          mkAttack({
            attack_result_id: 'ar-0',
            labels: { conversation_tree_id: 't-fan', tree_path: '[["attempt",0]]' },
          }),
          mkAttack({
            attack_result_id: 'ar-1',
            labels: { conversation_tree_id: 't-fan', tree_path: '[["attempt",1]]' },
          }),
        ]),
      ),
      getMessages: jest.fn(async () => mkMessages('conv-1')),
    }

    renderHook(() =>
      useReloadReconstruction({
        fragmentTreeId: 't-fan',
        currentTree: null,
        onTreeChange,
        onReconstructionDegraded,
        reloadApi: api,
      }),
    )

    await waitFor(() => expect(onTreeChange).toHaveBeenCalled())
    expect(onReconstructionDegraded).toHaveBeenCalledTimes(1)
    expect(onReconstructionDegraded.mock.calls[0][0]).toMatchObject({ fanCount: 1 })
  })

  it('does NOT disclose degradation for a purely linear AR set (no fans)', async () => {
    const onTreeChange = jest.fn()
    const onReconstructionDegraded = jest.fn()
    const api = {
      listAttacks: jest.fn(async () =>
        mkList([
          mkAttack({
            attack_result_id: 'ar-lin',
            labels: { conversation_tree_id: 't-lin', tree_path: '[]' },
          }),
        ]),
      ),
      getMessages: jest.fn(async () => mkMessages('conv-1')),
    }

    renderHook(() =>
      useReloadReconstruction({
        fragmentTreeId: 't-lin',
        currentTree: null,
        onTreeChange,
        onReconstructionDegraded,
        reloadApi: api,
      }),
    )

    await waitFor(() => expect(onTreeChange).toHaveBeenCalled())
    expect(onReconstructionDegraded).not.toHaveBeenCalled()
  })
}
)