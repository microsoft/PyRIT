// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { renderHook, waitFor } from '@testing-library/react'

import { useReloadReconstruction } from './useReloadReconstruction'
import type {
  AttackListResponse,
  AttackSummary,
  ComponentIdentifier,
  ConversationMessagesResponse,
} from '../../types'
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

function mkMessagesWithConverters(
  conv: string,
  converters: ComponentIdentifier[] = [],
): ConversationMessagesResponse {
  const base = mkMessages(conv)
  return {
    ...base,
    messages: base.messages.map((msg) =>
      msg.role === 'user'
        ? {
            ...msg,
            pieces: msg.pieces.map((piece) => ({
              ...piece,
              converter_identifiers: converters,
            })),
          }
        : msg,
    ),
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
      limit: 100,
      label: ['conversation_tree_id:t-frag'],
    })
    expect(api.getMessages).toHaveBeenCalledWith('ar-1', 'conv-1')

    const tree = onTreeChange.mock.calls[0][0] as ConversationTree
    expect(tree.id).toBe('t-frag')
    expect(tree.nodes.length).toBeGreaterThan(0)
  })

  it('hydrates the reconstructed root target from the base attack summary', async () => {
    const onTreeChange = jest.fn()
    const api = {
      listAttacks: jest.fn(async () =>
        mkList([
          mkAttack({
            labels: { conversation_tree_id: 't-frag' },
            target: { target_registry_name: 'RecoveredTarget::12345678', target_type: 'RecoveredTarget' },
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
    const root = tree.nodes.find((n) => n.id === tree.rootId)
    expect(root?.kind === 'root_prompt' ? root.params.targetRegistryName : null).toBe('RecoveredTarget::12345678')
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

  it('paginates reload lookup using the backend max page size', async () => {
    const onTreeChange = jest.fn()
    const first = mkAttack({
      attack_result_id: 'ar-page-1',
      conversation_id: 'conv-page-1',
      labels: { conversation_tree_id: 't-paged' },
    })
    const second = mkAttack({
      attack_result_id: 'ar-page-2',
      conversation_id: 'conv-page-2',
      created_at: '2026-06-11T00:01:00Z',
      labels: { conversation_tree_id: 't-paged' },
    })
    const api = {
      listAttacks: jest.fn(async (params?: { cursor?: string }) =>
        params?.cursor === 'next-page'
          ? {
              items: [second],
              pagination: { has_more: false, next_cursor: null, prev_cursor: null, limit: 100 },
            }
          : {
              items: [first],
              pagination: { has_more: true, next_cursor: 'next-page', prev_cursor: null, limit: 100 },
            },
      ),
      getMessages: jest.fn(async () => mkMessages('conv-page-1')),
    }

    renderHook(() =>
      useReloadReconstruction({
        fragmentTreeId: 't-paged',
        currentTree: null,
        onTreeChange,
        reloadApi: api,
      }),
    )

    await waitFor(() => expect(onTreeChange).toHaveBeenCalled())
    expect(api.listAttacks).toHaveBeenNthCalledWith(1, {
      limit: 100,
      label: ['conversation_tree_id:t-paged'],
    })
    expect(api.listAttacks).toHaveBeenNthCalledWith(2, {
      limit: 100,
      label: ['conversation_tree_id:t-paged'],
      cursor: 'next-page',
    })
    expect(api.getMessages).toHaveBeenCalledWith('ar-page-1', 'conv-page-1')
  })

  it('fully reconstructs a root-level attempt fan (no degradation banner)', async () => {
    // PR7g slice 2: a single root-level attempt fan reconstructs fully —
    // root → fan(attempt) → send×2 — so the degraded banner must NOT fire.
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
    expect(onReconstructionDegraded).not.toHaveBeenCalled()
    const tree = onTreeChange.mock.calls[0][0] as ConversationTree
    expect(tree.nodes.some((n) => n.kind === 'fan')).toBe(true)
  })

  it('reconstructs a root-level converter fan (no banner) and fetches each member leaf', async () => {
    // PR7g slice 3: a single root-level converter fan reconstructs fully via
    // per-leaf converter fetches; the degraded banner must NOT fire.
    const onTreeChange = jest.fn()
    const onReconstructionDegraded = jest.fn()
    const getMessages = jest.fn(async () => mkMessages('conv-1'))
    const api = {
      listAttacks: jest.fn(async () =>
        mkList([
          mkAttack({
            attack_result_id: 'ar-0',
            conversation_id: 'conv-0',
            labels: { conversation_tree_id: 't-conv', tree_path: '[["converter",0]]' },
          }),
          mkAttack({
            attack_result_id: 'ar-1',
            conversation_id: 'conv-1',
            labels: { conversation_tree_id: 't-conv', tree_path: '[["converter",1]]' },
          }),
        ]),
      ),
      getMessages,
    }

    renderHook(() =>
      useReloadReconstruction({
        fragmentTreeId: 't-conv',
        currentTree: null,
        onTreeChange,
        onReconstructionDegraded,
        reloadApi: api,
      }),
    )

    await waitFor(() => expect(onTreeChange).toHaveBeenCalled())
    expect(onReconstructionDegraded).not.toHaveBeenCalled()
    const tree = onTreeChange.mock.calls[0][0] as ConversationTree
    const fan = tree.nodes.find((n) => n.kind === 'fan')
    expect(fan?.kind).toBe('fan')
    // Base fetch (pickBaseAttack) + one per member leaf for the converter
    // resolver — at least the 2 member fetches happened.
    expect(getMessages).toHaveBeenCalledWith('ar-0', 'conv-0')
    expect(getMessages).toHaveBeenCalledWith('ar-1', 'conv-1')
  })

  it('reconstructs remaining converter-fan slots when one member message fetch fails', async () => {
    const onTreeChange = jest.fn()
    const onReconstructionDegraded = jest.fn()
    const converter: ComponentIdentifier = {
      class_name: 'PrefixConverter',
      class_module: 'pyrit.prompt_converter',
      params: { prefix: 'safe' },
    }
    const getMessages = jest.fn(async (arId: string, convId: string) => {
      if (arId === 'ar-1') throw new Error('transient leaf fetch')
      return mkMessagesWithConverters(convId, [converter])
    })
    const api = {
      listAttacks: jest.fn(async () =>
        mkList([
          mkAttack({
            attack_result_id: 'ar-0',
            conversation_id: 'conv-0',
            labels: { conversation_tree_id: 't-conv-partial', tree_path: '[["converter",0]]' },
          }),
          mkAttack({
            attack_result_id: 'ar-1',
            conversation_id: 'conv-1',
            labels: { conversation_tree_id: 't-conv-partial', tree_path: '[["converter",1]]' },
          }),
        ]),
      ),
      getMessages,
    }

    renderHook(() =>
      useReloadReconstruction({
        fragmentTreeId: 't-conv-partial',
        currentTree: null,
        onTreeChange,
        onReconstructionDegraded,
        reloadApi: api,
      }),
    )

    await waitFor(() => expect(onTreeChange).toHaveBeenCalled())
    expect(onReconstructionDegraded).toHaveBeenCalledWith({
      fanCount: 1,
      reason: 'converter_resolution',
    })
    const tree = onTreeChange.mock.calls[0][0] as ConversationTree
    const userTurns = tree.nodes.filter((n) => n.kind === 'user_turn')
    expect(userTurns).toHaveLength(2)
    expect(userTurns[0].params.converterPipeline).toEqual([
      { inline: { type: converter.class_name, params: converter.params } },
    ])
    expect(userTurns[1].params.converterPipeline).toBeUndefined()
  })

  it('discloses degraded reconstruction for a NESTED fan (deferred topology)', async () => {
    // Nested fans are not fan-aware-reconstructed; they fall back to a linear
    // chain and MUST disclose the loss rather than silently degrade.
    const onTreeChange = jest.fn()
    const onReconstructionDegraded = jest.fn()
    const api = {
      listAttacks: jest.fn(async () =>
        mkList([
          mkAttack({
            attack_result_id: 'n00',
            labels: { conversation_tree_id: 't-nested', tree_path: '[["prompt",0],["attempt",0]]' },
          }),
          mkAttack({
            attack_result_id: 'n01',
            labels: { conversation_tree_id: 't-nested', tree_path: '[["prompt",0],["attempt",1]]' },
          }),
          mkAttack({
            attack_result_id: 'n10',
            labels: { conversation_tree_id: 't-nested', tree_path: '[["prompt",1],["attempt",0]]' },
          }),
          mkAttack({
            attack_result_id: 'n11',
            labels: { conversation_tree_id: 't-nested', tree_path: '[["prompt",1],["attempt",1]]' },
          }),
        ]),
      ),
      getMessages: jest.fn(async () => mkMessages('conv-1')),
    }

    renderHook(() =>
      useReloadReconstruction({
        fragmentTreeId: 't-nested',
        currentTree: null,
        onTreeChange,
        onReconstructionDegraded,
        reloadApi: api,
      }),
    )

    await waitFor(() => expect(onTreeChange).toHaveBeenCalled())
    expect(onReconstructionDegraded).toHaveBeenCalledTimes(1)
    expect(onReconstructionDegraded.mock.calls[0][0].fanCount).toBeGreaterThanOrEqual(2)
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
