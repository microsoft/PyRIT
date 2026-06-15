// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Tests for the auto-reverse pure functions per spec 01 §9.3 + §9.3.1.
 *
 * Three entry points:
 *   - `parseTreePath(s)` — decode a leaf AR's `labels.tree_path`
 *     (JSON-encoded `[[axis, slotIndex], ...]`).
 *   - `linearChainFromMessages(messages)` — fast path (01 §9.3) for
 *     single-conversation ARs: build a root→user→send→user→send chain
 *     from a `BackendMessage[]`. System messages either hoist into
 *     `RootPromptNode.systemPrompt` or land as `UserTurnNode{role:'system'}`
 *     at the chain head.
 *   - `detectFansV10Plus(leaves)` — Algorithm 1 (01 §9.3.1) — decodes
 *     `tree_path` per leaf, groups by `(parent_path, axis)`, returns one
 *     `ImplicitFan` per group of ≥2 leaves.
 *   - `reconstructVariantPayloads(fan, resolver)` — derives the
 *     per-slot `FanVariant` array for axes `attempt` (empty payload)
 *     and `converter` (consensus by most-frequent, warning chip on
 *     divergence). Other axes throw — V1.1+ adds them.
 */

import {
  parseTreePath,
  linearChainFromMessages,
  mergedTreeFromConversations,
  detectFansV10Plus,
  reconstructVariantPayloads,
  reconstructTreeWithFans,
  type ImplicitFan,
  type LeafForFanDetection,
} from './autoReverse'
import type { BackendMessage, BackendMessagePiece, ComponentIdentifier } from '../types'

// ============================================================================
// Fixture helpers
// ============================================================================

function mkPiece(opts: {
  pieceId: string
  value: string
  originalPromptId?: string | null
  converterIdentifiers?: ComponentIdentifier[]
}): BackendMessagePiece {
  return {
    piece_id: opts.pieceId,
    original_value_data_type: 'text',
    converted_value_data_type: 'text',
    original_value: opts.value,
    converted_value: opts.value,
    scores: [],
    response_error: 'none',
    original_prompt_id: opts.originalPromptId ?? opts.pieceId,
    converter_identifiers: opts.converterIdentifiers ?? [],
  }
}

function mkMessage(opts: {
  turnNumber: number
  role: 'user' | 'assistant' | 'system' | 'simulated_assistant'
  pieces: BackendMessagePiece[]
  createdAt?: string
}): BackendMessage {
  return {
    turn_number: opts.turnNumber,
    role: opts.role,
    pieces: opts.pieces,
    created_at: opts.createdAt ?? '2026-06-11T00:00:00Z',
  }
}

function mkConverter(className: string): ComponentIdentifier {
  return {
    class_name: className,
    class_module: 'pyrit.prompt_converter',
    params: {},
  }
}

function mkLeaf(opts: {
  id: string
  treePath: string
  // Lifted into `labels.tree_path`.
}): LeafForFanDetection {
  return {
    attack_result_id: opts.id,
    labels: { tree_path: opts.treePath },
  }
}

// ============================================================================
// parseTreePath
// ============================================================================

describe('parseTreePath', () => {
  it('returns [] for empty string', () => {
    expect(parseTreePath('')).toEqual([])
  })

  it('parses a single segment', () => {
    expect(parseTreePath('[["attempt",2]]')).toEqual([['attempt', 2]])
  })

  it('parses a nested path', () => {
    expect(parseTreePath('[["prompt",1],["attempt",3]]')).toEqual([
      ['prompt', 1],
      ['attempt', 3],
    ])
  })

  it('returns [] on malformed JSON (defensive — never crash)', () => {
    expect(parseTreePath('not-json')).toEqual([])
    expect(parseTreePath('{')).toEqual([])
  })

  it('returns [] on JSON that is not an array', () => {
    expect(parseTreePath('{"axis":"attempt"}')).toEqual([])
    expect(parseTreePath('null')).toEqual([])
    expect(parseTreePath('42')).toEqual([])
  })

  it('returns [] on JSON array whose entries are not [string, number] pairs', () => {
    expect(parseTreePath('[["attempt"]]')).toEqual([])
    expect(parseTreePath('[[42, "attempt"]]')).toEqual([])
    expect(parseTreePath('[["attempt", "two"]]')).toEqual([])
  })
})

// ============================================================================
// linearChainFromMessages
// ============================================================================

describe('linearChainFromMessages', () => {
  it('produces a root + send for a single user→assistant exchange', () => {
    const tree = linearChainFromMessages([
      mkMessage({
        turnNumber: 1,
        role: 'user',
        pieces: [mkPiece({ pieceId: 'p1', value: 'Hello' })],
      }),
      mkMessage({
        turnNumber: 2,
        role: 'assistant',
        pieces: [mkPiece({ pieceId: 'p2', value: 'Hi there' })],
      }),
    ])
    const root = tree.nodes.find((n) => n.id === tree.rootId)
    expect(root?.kind).toBe('root_prompt')
    if (root?.kind !== 'root_prompt') throw new Error('root missing')
    expect(root.params.text).toBe('Hello')
    // One Send (the assistant reply).
    const sends = tree.nodes.filter((n) => n.kind === 'send')
    expect(sends).toHaveLength(1)
    expect(sends[0].params.responsePreview).toBe('Hi there')
  })

  it('hydrates the root target registry name when provided by the caller', () => {
    const tree = linearChainFromMessages(
      [
        mkMessage({
          turnNumber: 1,
          role: 'user',
          pieces: [mkPiece({ pieceId: 'p1', value: 'Hello' })],
        }),
        mkMessage({ turnNumber: 2, role: 'assistant', pieces: [mkPiece({ pieceId: 'p2', value: 'Hi' })] }),
      ],
      { targetRegistryName: 'OpenAIChatTarget::abcd1234' },
    )
    const root = tree.nodes.find((n) => n.id === tree.rootId)
    if (root?.kind !== 'root_prompt') throw new Error('root missing')
    expect(root.params.targetRegistryName).toBe('OpenAIChatTarget::abcd1234')
  })

  it('produces alternating UserTurn → Send → UserTurn → Send for multi-turn', () => {
    const tree = linearChainFromMessages([
      mkMessage({ turnNumber: 1, role: 'user', pieces: [mkPiece({ pieceId: 'p1', value: 'U1' })] }),
      mkMessage({ turnNumber: 2, role: 'assistant', pieces: [mkPiece({ pieceId: 'p2', value: 'A1' })] }),
      mkMessage({ turnNumber: 3, role: 'user', pieces: [mkPiece({ pieceId: 'p3', value: 'U2' })] }),
      mkMessage({ turnNumber: 4, role: 'assistant', pieces: [mkPiece({ pieceId: 'p4', value: 'A2' })] }),
    ])
    // Linear chain: root, send, user, send. 4 nodes total.
    expect(tree.nodes).toHaveLength(4)
    expect(tree.nodes[0].kind).toBe('root_prompt')
    expect(tree.nodes[1].kind).toBe('send')
    expect(tree.nodes[2].kind).toBe('user_turn')
    expect(tree.nodes[3].kind).toBe('send')
    expect(tree.nodes[1].kind === 'send' ? tree.nodes[1].params.responsePreview : null).toBe('A1')
    expect(tree.nodes[3].kind === 'send' ? tree.nodes[3].params.responsePreview : null).toBe('A2')
    // Edge slot indices all 0 (linear chain, no fans).
    expect(tree.edges.every((e) => e.slotIndex === 0)).toBe(true)
  })

  it('hydrates UserTurn.converterPipeline from MessagePiece.converter_identifiers', () => {
    const conv = mkConverter('Base64Converter')
    const tree = linearChainFromMessages([
      mkMessage({
        turnNumber: 1,
        role: 'user',
        pieces: [mkPiece({ pieceId: 'p1', value: 'Hello', converterIdentifiers: [conv] })],
      }),
      mkMessage({ turnNumber: 2, role: 'assistant', pieces: [mkPiece({ pieceId: 'p2', value: 'Hi' })] }),
      mkMessage({
        turnNumber: 3,
        role: 'user',
        pieces: [mkPiece({ pieceId: 'p3', value: 'U2', converterIdentifiers: [conv, mkConverter('RotConverter')] })],
      }),
      mkMessage({ turnNumber: 4, role: 'assistant', pieces: [mkPiece({ pieceId: 'p4', value: 'A2' })] }),
    ])
    // Root carries first user's converter pipeline as its own input bundle is not
    // a UserTurn; root params do NOT include converterPipeline. Verify second
    // user-turn (index 2) holds the 2-converter pipeline.
    const secondUser = tree.nodes[2]
    if (secondUser.kind !== 'user_turn') throw new Error('expected user_turn at index 2')
    expect(secondUser.params.converterPipeline).toHaveLength(2)
    expect(secondUser.params.converterPipeline?.[0].inline?.type).toBe('Base64Converter')
    expect(secondUser.params.converterPipeline?.[1].inline?.type).toBe('RotConverter')
  })

  it('hoists a leading system message into RootPromptNode.systemPrompt', () => {
    const tree = linearChainFromMessages([
      mkMessage({ turnNumber: 1, role: 'system', pieces: [mkPiece({ pieceId: 'sys', value: 'You are a helper' })] }),
      mkMessage({ turnNumber: 2, role: 'user', pieces: [mkPiece({ pieceId: 'p1', value: 'Hello' })] }),
      mkMessage({ turnNumber: 3, role: 'assistant', pieces: [mkPiece({ pieceId: 'p2', value: 'Hi' })] }),
    ])
    const root = tree.nodes.find((n) => n.id === tree.rootId)
    if (root?.kind !== 'root_prompt') throw new Error('root missing')
    expect(root.params.systemPrompt).toBe('You are a helper')
    expect(root.params.text).toBe('Hello')
    // No standalone user_turn for the system message.
    const userTurns = tree.nodes.filter((n) => n.kind === 'user_turn')
    expect(userTurns).toHaveLength(0)
  })

  it('produces a simulated_assistant UserTurn (not Send) for role=simulated_assistant', () => {
    const tree = linearChainFromMessages([
      mkMessage({ turnNumber: 1, role: 'user', pieces: [mkPiece({ pieceId: 'p1', value: 'Hello' })] }),
      mkMessage({ turnNumber: 2, role: 'simulated_assistant', pieces: [mkPiece({ pieceId: 'p2', value: 'Pretend' })] }),
      mkMessage({ turnNumber: 3, role: 'user', pieces: [mkPiece({ pieceId: 'p3', value: 'U2' })] }),
      mkMessage({ turnNumber: 4, role: 'assistant', pieces: [mkPiece({ pieceId: 'p4', value: 'A2' })] }),
    ])
    // Simulated assistant becomes a UserTurnNode with role='simulated_assistant'.
    const sims = tree.nodes.filter(
      (n) => n.kind === 'user_turn' && n.params.role === 'simulated_assistant',
    )
    expect(sims).toHaveLength(1)
  })

  it('returns an empty greenfield tree when given zero messages', () => {
    const tree = linearChainFromMessages([])
    // No root => caller surfaces a fail-soft greenfield; the function still
    // returns a syntactically-valid empty tree shape rather than throwing.
    expect(tree.nodes).toEqual([])
    expect(tree.edges).toEqual([])
  })

  it('returns an empty greenfield tree when given only system messages (no first user turn)', () => {
    const tree = linearChainFromMessages([
      mkMessage({ turnNumber: 1, role: 'system', pieces: [mkPiece({ pieceId: 'sys', value: 'Hint' })] }),
    ])
    // Defensive: an all-system payload has no first user turn to seed the
    // root; the function returns an empty tree rather than crash.
    expect(tree.nodes).toEqual([])
    expect(tree.edges).toEqual([])
  })

  it('returns an empty greenfield tree when first non-system message is assistant (malformed)', () => {
    const tree = linearChainFromMessages([
      mkMessage({
        turnNumber: 1,
        role: 'assistant',
        pieces: [mkPiece({ pieceId: 'p1', value: 'A1' })],
      }),
    ])
    expect(tree.nodes).toEqual([])
  })
})

// ============================================================================
// mergedTreeFromConversations
// ============================================================================

describe('mergedTreeFromConversations', () => {
  it('merges identical prefixes and branches at the first divergent turn', () => {
    const result = mergedTreeFromConversations([
      {
        conversation_id: 'conv-a',
        messages: [
          mkMessage({ turnNumber: 1, role: 'user', pieces: [mkPiece({ pieceId: 'a-p1', value: 'Root' })] }),
          mkMessage({ turnNumber: 2, role: 'assistant', pieces: [mkPiece({ pieceId: 'a-p2', value: 'A1' })] }),
          mkMessage({ turnNumber: 3, role: 'user', pieces: [mkPiece({ pieceId: 'a-p3', value: 'Follow A' })] }),
          mkMessage({ turnNumber: 4, role: 'assistant', pieces: [mkPiece({ pieceId: 'a-p4', value: 'Answer A' })] }),
        ],
      },
      {
        conversation_id: 'conv-b',
        messages: [
          mkMessage({ turnNumber: 1, role: 'user', pieces: [mkPiece({ pieceId: 'b-p1', value: 'Root' })] }),
          mkMessage({ turnNumber: 2, role: 'assistant', pieces: [mkPiece({ pieceId: 'b-p2', value: 'A1' })] }),
          mkMessage({ turnNumber: 3, role: 'user', pieces: [mkPiece({ pieceId: 'b-p3', value: 'Follow B' })] }),
          mkMessage({ turnNumber: 4, role: 'assistant', pieces: [mkPiece({ pieceId: 'b-p4', value: 'Answer B' })] }),
        ],
      },
    ])

    expect(result.includedConversationIds).toEqual(['conv-a', 'conv-b'])
    expect(result.omittedConversationIds).toEqual([])
    expect(result.tree.nodes.filter((node) => node.kind === 'send')).toHaveLength(3)
    const sharedSend = result.tree.nodes.find(
      (node) => node.kind === 'send' && node.params.responsePreview === 'A1',
    )
    expect(sharedSend).toBeDefined()
    const branchUserTurns = result.tree.nodes.filter(
      (node) => node.kind === 'user_turn' && node.parentId === sharedSend?.id,
    )
    expect(branchUserTurns.map((node) => node.kind === 'user_turn' ? node.params.text : '').sort()).toEqual([
      'Follow A',
      'Follow B',
    ])
  })

  it('omits conversations whose root prompt cannot be reconciled with the base root', () => {
    const result = mergedTreeFromConversations([
      {
        conversation_id: 'conv-a',
        messages: [
          mkMessage({ turnNumber: 1, role: 'user', pieces: [mkPiece({ pieceId: 'a-p1', value: 'Root A' })] }),
          mkMessage({ turnNumber: 2, role: 'assistant', pieces: [mkPiece({ pieceId: 'a-p2', value: 'A1' })] }),
        ],
      },
      {
        conversation_id: 'conv-b',
        messages: [
          mkMessage({ turnNumber: 1, role: 'user', pieces: [mkPiece({ pieceId: 'b-p1', value: 'Root B' })] }),
          mkMessage({ turnNumber: 2, role: 'assistant', pieces: [mkPiece({ pieceId: 'b-p2', value: 'B1' })] }),
        ],
      },
    ])

    expect(result.includedConversationIds).toEqual(['conv-a'])
    expect(result.omittedConversationIds).toEqual(['conv-b'])
    const root = result.tree.nodes.find((node) => node.id === result.tree.rootId)
    expect(root?.kind === 'root_prompt' ? root.params.text : null).toBe('Root A')
  })
})

// ============================================================================
// detectFansV10Plus
// ============================================================================

describe('detectFansV10Plus', () => {
  it('returns [] when no leaves have a tree_path', () => {
    const leaves: LeafForFanDetection[] = [
      mkLeaf({ id: 'l1', treePath: '' }),
      mkLeaf({ id: 'l2', treePath: '' }),
    ]
    expect(detectFansV10Plus(leaves)).toEqual([])
  })

  it('returns [] when a tree_path has only a single leaf at it (no siblings)', () => {
    const leaves: LeafForFanDetection[] = [
      mkLeaf({ id: 'lone', treePath: '[["attempt",0]]' }),
    ]
    expect(detectFansV10Plus(leaves)).toEqual([])
  })

  it('groups 2+ leaves sharing parent_path+axis into one fan', () => {
    const leaves: LeafForFanDetection[] = [
      mkLeaf({ id: 'a', treePath: '[["attempt",0]]' }),
      mkLeaf({ id: 'b', treePath: '[["attempt",1]]' }),
      mkLeaf({ id: 'c', treePath: '[["attempt",2]]' }),
    ]
    const fans = detectFansV10Plus(leaves)
    expect(fans).toHaveLength(1)
    expect(fans[0].axis).toBe('attempt')
    expect(fans[0].parent_path).toEqual([])
    expect(fans[0].member_slot_indices.sort()).toEqual([0, 1, 2])
    expect(fans[0].member_ars.map((l) => l.attack_result_id).sort()).toEqual(['a', 'b', 'c'])
  })

  it('produces TWO fans at the same parent_path when leaves carry different axes (axis change mid-tree)', () => {
    // Spec §9.3.1: "operators CAN change a fan's axis mid-tree ... split into
    // one ImplicitFan per axis at the same parent_path so the operator sees
    // the post-hoc structure honestly."
    const leaves: LeafForFanDetection[] = [
      mkLeaf({ id: 'a1', treePath: '[["attempt",0]]' }),
      mkLeaf({ id: 'a2', treePath: '[["attempt",1]]' }),
      mkLeaf({ id: 'c1', treePath: '[["converter",0]]' }),
      mkLeaf({ id: 'c2', treePath: '[["converter",1]]' }),
    ]
    const fans = detectFansV10Plus(leaves).sort((x, y) => x.axis.localeCompare(y.axis))
    expect(fans).toHaveLength(2)
    expect(fans[0].axis).toBe('attempt')
    expect(fans[1].axis).toBe('converter')
  })

  it('handles nested fans: outer prompt-fan + inner attempt-fans', () => {
    // Two outer slots ("prompt",0) and ("prompt",1). Each holds an inner
    // attempt-fan with 2+ slots. Expect: 1 outer prompt-fan at [] + 2 inner
    // attempt-fans at [("prompt",0)] and [("prompt",1)].
    const leaves: LeafForFanDetection[] = [
      mkLeaf({ id: 'p0a0', treePath: '[["prompt",0],["attempt",0]]' }),
      mkLeaf({ id: 'p0a1', treePath: '[["prompt",0],["attempt",1]]' }),
      mkLeaf({ id: 'p1a0', treePath: '[["prompt",1],["attempt",0]]' }),
      mkLeaf({ id: 'p1a1', treePath: '[["prompt",1],["attempt",1]]' }),
    ]
    const fans = detectFansV10Plus(leaves)
    // Sort for deterministic assertions.
    const byKey = new Map<string, ImplicitFan>()
    for (const f of fans) {
      byKey.set(`${JSON.stringify(f.parent_path)}|${f.axis}`, f)
    }
    // Outer prompt fan at root (parent_path = []).
    const outer = byKey.get(`[]|prompt`)
    expect(outer).toBeDefined()
    expect(outer?.member_slot_indices.sort()).toEqual([0, 0, 1, 1])
    // Inner attempt-fan under ("prompt", 0).
    const inner0 = byKey.get(`[["prompt",0]]|attempt`)
    expect(inner0).toBeDefined()
    expect(inner0?.member_slot_indices.sort()).toEqual([0, 1])
    // Inner attempt-fan under ("prompt", 1).
    const inner1 = byKey.get(`[["prompt",1]]|attempt`)
    expect(inner1).toBeDefined()
    expect(inner1?.member_slot_indices.sort()).toEqual([0, 1])
  })
})

// ============================================================================
// reconstructVariantPayloads
// ============================================================================

describe('reconstructVariantPayloads', () => {
  function mkAttemptFan(slotIndices: number[]): ImplicitFan {
    return {
      parent_path: [],
      axis: 'attempt',
      member_ars: slotIndices.map((s) => mkLeaf({ id: `s${s}`, treePath: `[["attempt",${s}]]` })),
      member_slot_indices: slotIndices,
    }
  }

  function mkConverterFan(opts: {
    slotIndices: number[]
    /** Per-leaf converter lists, parallel to slotIndices. */
    perLeafConverters: ComponentIdentifier[][]
  }): ImplicitFan {
    return {
      parent_path: [],
      axis: 'converter',
      member_ars: opts.slotIndices.map((s, i) => ({
        attack_result_id: `s${s}_${i}`,
        labels: { tree_path: `[["converter",${s}]]` },
        // The resolver in the test reads this back.
        _testConverters: opts.perLeafConverters[i],
      })) as LeafForFanDetection[],
      member_slot_indices: opts.slotIndices,
    }
  }

  // Resolver used by all converter tests: pulls converters off the leaf's
  // test-side annotation.
  function testConverterResolver(
    leaf: LeafForFanDetection,
  ): ComponentIdentifier[] {
    return (leaf as LeafForFanDetection & { _testConverters?: ComponentIdentifier[] })
      ._testConverters ?? []
  }

  it('axis=attempt: all slots return { axis: attempt, payload: {} }', () => {
    const fan = mkAttemptFan([0, 1, 2])
    const variants = reconstructVariantPayloads(fan, testConverterResolver)
    expect(variants).toHaveLength(3)
    for (const v of variants) {
      expect(v.axis).toBe('attempt')
      expect(v.payload).toEqual({})
    }
  })

  it('axis=attempt: max(slot)+1 length even when slots have gaps (deleted slot tombstones)', () => {
    const fan = mkAttemptFan([0, 2])
    const variants = reconstructVariantPayloads(fan, testConverterResolver)
    expect(variants).toHaveLength(3)
    // Gaps are filled with the same empty payload.
    expect(variants[1].payload).toEqual({})
  })

  it('axis=converter: single-leaf-per-slot reads each leaf converter list into its slot', () => {
    const c1 = mkConverter('Base64Converter')
    const c2 = mkConverter('RotConverter')
    const fan = mkConverterFan({
      slotIndices: [0, 1],
      perLeafConverters: [[c1], [c2]],
    })
    const variants = reconstructVariantPayloads(fan, testConverterResolver)
    expect(variants).toHaveLength(2)
    if (variants[0].axis !== 'converter' || variants[1].axis !== 'converter') {
      throw new Error('expected converter variants')
    }
    expect(variants[0].payload.converters[0].inline?.type).toBe('Base64Converter')
    expect(variants[1].payload.converters[0].inline?.type).toBe('RotConverter')
  })

  it('axis=converter: gap slot (no leaves) gets empty converter list', () => {
    const c1 = mkConverter('Base64Converter')
    const fan = mkConverterFan({
      slotIndices: [0, 2],
      perLeafConverters: [[c1], [c1]],
    })
    const variants = reconstructVariantPayloads(fan, testConverterResolver)
    expect(variants).toHaveLength(3)
    if (variants[1].axis !== 'converter') throw new Error('expected converter')
    expect(variants[1].payload.converters).toEqual([])
  })

  it('axis=converter: multi-leaf-per-slot agreement → consensus, no divergence warning', () => {
    const c1 = mkConverter('Base64Converter')
    const warnings: Array<{ slot: number }> = []
    const fan = mkConverterFan({
      slotIndices: [0, 0, 0],
      perLeafConverters: [[c1], [c1], [c1]],
    })
    const variants = reconstructVariantPayloads(fan, testConverterResolver, {
      onDivergence: (slot) => warnings.push({ slot }),
    })
    expect(warnings).toEqual([])
    if (variants[0].axis !== 'converter') throw new Error('expected converter')
    expect(variants[0].payload.converters[0].inline?.type).toBe('Base64Converter')
  })

  it('axis=converter: multi-leaf-per-slot disagreement → most-frequent wins + warning emitted', () => {
    // Spec §9.3.1: "if leaves at slot s disagree ... the algorithm picks the
    // most-frequent value across member_ars at s and renders a warning chip"
    const c1 = mkConverter('Base64Converter')
    const c2 = mkConverter('RotConverter')
    const warnings: Array<{ slot: number }> = []
    const fan = mkConverterFan({
      slotIndices: [0, 0, 0],
      perLeafConverters: [[c1], [c1], [c2]],
    })
    const variants = reconstructVariantPayloads(fan, testConverterResolver, {
      onDivergence: (slot) => warnings.push({ slot }),
    })
    expect(warnings).toEqual([{ slot: 0 }])
    if (variants[0].axis !== 'converter') throw new Error('expected converter')
    // Most frequent: c1 (2 votes) over c2 (1 vote).
    expect(variants[0].payload.converters[0].inline?.type).toBe('Base64Converter')
  })

  it('throws NotImplemented for V1.1+ axes (prompt / target / system_prompt / temperature)', () => {
    const promptFan: ImplicitFan = {
      parent_path: [],
      axis: 'prompt',
      member_ars: [mkLeaf({ id: 'p0', treePath: '[["prompt",0]]' })],
      member_slot_indices: [0],
    }
    expect(() => reconstructVariantPayloads(promptFan, testConverterResolver)).toThrow(
      /not.*implement/i,
    )
  })
})

// ============================================================================
// reconstructTreeWithFans (PR7g slice 2) — fan-aware tree assembly
// ============================================================================

describe('reconstructTreeWithFans', () => {
  function userMsg(turn: number, value: string): BackendMessage {
    return mkMessage({ turnNumber: turn, role: 'user', pieces: [mkPiece({ pieceId: `u${turn}`, value })] })
  }
  function asstMsg(turn: number, value: string): BackendMessage {
    return mkMessage({ turnNumber: turn, role: 'assistant', pieces: [mkPiece({ pieceId: `a${turn}`, value })] })
  }

  it('no fans → linear reconstruction, fullyReconstructed=true, fanCount=0', () => {
    const result = reconstructTreeWithFans({
      baseMessages: [userMsg(1, 'hello'), asstMsg(2, 'hi')],
      leaves: [mkLeaf({ id: 'l1', treePath: '' })],
    })
    expect(result.fanCount).toBe(0)
    expect(result.fullyReconstructed).toBe(true)
    // root + send, no fan node.
    expect(result.tree.nodes.some((n) => n.kind === 'fan')).toBe(false)
    expect(result.tree.nodes.find((n) => n.id === result.tree.rootId)?.kind).toBe('root_prompt')
  })

  it('root-level attempt fan (base = user→assistant) → root → fan(attempt, N) → send×N', () => {
    const result = reconstructTreeWithFans({
      baseMessages: [userMsg(1, 'prompt'), asstMsg(2, 'resp-slot0')],
      leaves: [
        mkLeaf({ id: 'a0', treePath: '[["attempt",0]]' }),
        mkLeaf({ id: 'a1', treePath: '[["attempt",1]]' }),
        mkLeaf({ id: 'a2', treePath: '[["attempt",2]]' }),
      ],
    })
    expect(result.fanCount).toBe(1)
    expect(result.fullyReconstructed).toBe(true)

    const root = result.tree.nodes.find((n) => n.id === result.tree.rootId)
    expect(root?.kind).toBe('root_prompt')

    const fan = result.tree.nodes.find((n) => n.kind === 'fan')
    if (fan?.kind !== 'fan') throw new Error('expected a fan node')
    expect(fan.params.axis).toBe('attempt')
    expect(fan.params.variants).toHaveLength(3)
    expect(fan.params.variants.every((v) => v.axis === 'attempt')).toBe(true)
    // Fan attaches directly to the root (2-message base = no intermediate turns).
    expect(fan.parentId).toBe(result.tree.rootId)

    // Three send children, one per slot, with distinct edge slotIndices.
    const fanChildEdges = result.tree.edges.filter((e) => e.parentId === fan.id)
    expect(fanChildEdges).toHaveLength(3)
    expect(fanChildEdges.map((e) => e.slotIndex).sort()).toEqual([0, 1, 2])
    for (const edge of fanChildEdges) {
      const child = result.tree.nodes.find((n) => n.id === edge.childId)
      expect(child?.kind).toBe('send')
    }
  })

  it('root-level attempt fan with an intermediate turn → fan attaches to the spine tip (user_turn)', () => {
    // base = user → assistant → user → assistant. The LAST assistant is the
    // fanned divergence; the spine is root → send → user_turn.
    const result = reconstructTreeWithFans({
      baseMessages: [userMsg(1, 'q1'), asstMsg(2, 'a1'), userMsg(3, 'q2'), asstMsg(4, 'a2-slot0')],
      leaves: [
        mkLeaf({ id: 'b0', treePath: '[["attempt",0]]' }),
        mkLeaf({ id: 'b1', treePath: '[["attempt",1]]' }),
      ],
    })
    expect(result.fullyReconstructed).toBe(true)
    const fan = result.tree.nodes.find((n) => n.kind === 'fan')
    if (fan?.kind !== 'fan') throw new Error('expected a fan node')
    // Fan's parent is the spine tip — a user_turn (turn 3), not the root.
    const parent = result.tree.nodes.find((n) => n.id === fan.parentId)
    expect(parent?.kind).toBe('user_turn')
    // Two send children.
    expect(result.tree.edges.filter((e) => e.parentId === fan.id)).toHaveLength(2)
  })

  it('converter fan → NOT fully reconstructed (degraded), linear fallback, fanCount=1', () => {
    const result = reconstructTreeWithFans({
      baseMessages: [userMsg(1, 'prompt'), asstMsg(2, 'resp')],
      leaves: [
        mkLeaf({ id: 'c0', treePath: '[["converter",0]]' }),
        mkLeaf({ id: 'c1', treePath: '[["converter",1]]' }),
      ],
    })
    expect(result.fanCount).toBe(1)
    expect(result.fullyReconstructed).toBe(false)
    // Linear fallback: no fan node (no converterResolver supplied).
    expect(result.tree.nodes.some((n) => n.kind === 'fan')).toBe(false)
  })

  it('root-level converter fan WITH a resolver → root → fan(converter) → [user_turn(converter) → send]×N', () => {
    const base64 = mkConverter('Base64Converter')
    const rot13 = mkConverter('RotConverter')
    const perLeaf: Record<string, ComponentIdentifier[]> = {
      c0: [base64],
      c1: [rot13],
    }
    const result = reconstructTreeWithFans({
      baseMessages: [userMsg(1, 'prompt'), asstMsg(2, 'resp')],
      leaves: [
        mkLeaf({ id: 'c0', treePath: '[["converter",0]]' }),
        mkLeaf({ id: 'c1', treePath: '[["converter",1]]' }),
      ],
      converterResolver: (leaf) => perLeaf[leaf.attack_result_id] ?? [],
    })

    expect(result.fanCount).toBe(1)
    expect(result.fullyReconstructed).toBe(true)

    const fan = result.tree.nodes.find((n) => n.kind === 'fan')
    if (fan?.kind !== 'fan') throw new Error('expected a fan node')
    expect(fan.params.axis).toBe('converter')
    expect(fan.parentId).toBe(result.tree.rootId)
    expect(fan.params.variants).toHaveLength(2)

    // Each fan child is a user_turn carrying that slot's converter pipeline,
    // and each user_turn has a single send child.
    const fanChildEdges = result.tree.edges
      .filter((e) => e.parentId === fan.id)
      .sort((a, b) => a.slotIndex - b.slotIndex)
    expect(fanChildEdges.map((e) => e.slotIndex)).toEqual([0, 1])

    const slotConverterTypes: string[] = []
    for (const edge of fanChildEdges) {
      const child = result.tree.nodes.find((n) => n.id === edge.childId)
      if (child?.kind !== 'user_turn') throw new Error('expected user_turn fan child')
      slotConverterTypes.push(child.params.converterPipeline?.[0]?.inline?.type ?? '')
      // Each user_turn has exactly one send child.
      const sendEdges = result.tree.edges.filter((e) => e.parentId === child.id)
      expect(sendEdges).toHaveLength(1)
      const send = result.tree.nodes.find((n) => n.id === sendEdges[0].childId)
      expect(send?.kind).toBe('send')
    }
    expect(slotConverterTypes).toEqual(['Base64Converter', 'RotConverter'])
  })

  it('converter fan uses original_value (authored prompt), not the converted gibberish, for node text', () => {
    const base64 = mkConverter('Base64Converter')
    // userMsg sets original_value === converted_value === the arg; build a
    // message where they differ to prove we pick original_value.
    const convertedFirst: BackendMessage = {
      turn_number: 1,
      role: 'user',
      pieces: [{
        piece_id: 'p1',
        original_value_data_type: 'text',
        converted_value_data_type: 'text',
        original_value: 'authored prompt',
        converted_value: 'YXV0aG9yZWQ=',
        scores: [],
        response_error: 'none',
        original_prompt_id: 'p1',
        converter_identifiers: [base64],
      }],
      created_at: '2026-06-11T00:00:00Z',
    }
    const result = reconstructTreeWithFans({
      baseMessages: [convertedFirst, asstMsg(2, 'resp')],
      leaves: [
        mkLeaf({ id: 'c0', treePath: '[["converter",0]]' }),
        mkLeaf({ id: 'c1', treePath: '[["converter",1]]' }),
      ],
      converterResolver: () => [base64],
    })
    const root = result.tree.nodes.find((n) => n.id === result.tree.rootId)
    if (root?.kind !== 'root_prompt') throw new Error('expected root')
    expect(root.params.text).toBe('authored prompt')
  })

  it('converter fan with per-slot divergence → most-frequent wins + onConverterDivergence fires', () => {
    const base64 = mkConverter('Base64Converter')
    const rot13 = mkConverter('RotConverter')
    // Slot 0 has three member leaves that disagree (2× base64, 1× rot13).
    const perLeaf: Record<string, ComponentIdentifier[]> = {
      a: [base64],
      b: [base64],
      c: [rot13],
    }
    const diverged: number[] = []
    const result = reconstructTreeWithFans({
      baseMessages: [userMsg(1, 'prompt'), asstMsg(2, 'resp')],
      leaves: [
        mkLeaf({ id: 'a', treePath: '[["converter",0]]' }),
        mkLeaf({ id: 'b', treePath: '[["converter",0]]' }),
        mkLeaf({ id: 'c', treePath: '[["converter",0]]' }),
        mkLeaf({ id: 'd', treePath: '[["converter",1]]' }),
      ],
      converterResolver: (leaf) => perLeaf[leaf.attack_result_id] ?? [rot13],
      onConverterDivergence: (slot) => diverged.push(slot),
    })
    expect(result.fullyReconstructed).toBe(true)
    expect(diverged).toEqual([0])
    const fan = result.tree.nodes.find((n) => n.kind === 'fan')
    if (fan?.kind !== 'fan' || fan.params.axis !== 'converter') throw new Error('expected converter fan')
    // Slot 0 consensus is base64 (2 of 3).
    expect(fan.params.variants[0].axis).toBe('converter')
    if (fan.params.variants[0].axis !== 'converter') throw new Error('narrow')
    expect(fan.params.variants[0].payload.converters[0].inline?.type).toBe('Base64Converter')
  })

  it('converter fan with NON-contiguous slots → degraded (no tombstone guessing)', () => {
    const base64 = mkConverter('Base64Converter')
    const result = reconstructTreeWithFans({
      baseMessages: [userMsg(1, 'prompt'), asstMsg(2, 'resp')],
      leaves: [
        mkLeaf({ id: 'c0', treePath: '[["converter",0]]' }),
        mkLeaf({ id: 'c2', treePath: '[["converter",2]]' }),
      ],
      converterResolver: () => [base64],
    })
    expect(result.fullyReconstructed).toBe(false)
    expect(result.tree.nodes.some((n) => n.kind === 'fan')).toBe(false)
  })

  it('nested fans → NOT fully reconstructed (degraded), fanCount reflects detected fans', () => {
    const result = reconstructTreeWithFans({
      baseMessages: [userMsg(1, 'prompt'), asstMsg(2, 'resp')],
      leaves: [
        mkLeaf({ id: 'n00', treePath: '[["prompt",0],["attempt",0]]' }),
        mkLeaf({ id: 'n01', treePath: '[["prompt",0],["attempt",1]]' }),
        mkLeaf({ id: 'n10', treePath: '[["prompt",1],["attempt",0]]' }),
        mkLeaf({ id: 'n11', treePath: '[["prompt",1],["attempt",1]]' }),
      ],
    })
    expect(result.fullyReconstructed).toBe(false)
    expect(result.fanCount).toBeGreaterThanOrEqual(2)
    expect(result.tree.nodes.some((n) => n.kind === 'fan')).toBe(false)
  })

  it('two distinct-axis fans at the same parent_path → degraded (ambiguous to reconstruct)', () => {
    const result = reconstructTreeWithFans({
      baseMessages: [userMsg(1, 'prompt'), asstMsg(2, 'resp')],
      leaves: [
        mkLeaf({ id: 'x0', treePath: '[["attempt",0]]' }),
        mkLeaf({ id: 'x1', treePath: '[["attempt",1]]' }),
        mkLeaf({ id: 'y0', treePath: '[["converter",0]]' }),
        mkLeaf({ id: 'y1', treePath: '[["converter",1]]' }),
      ],
    })
    expect(result.fullyReconstructed).toBe(false)
    expect(result.fanCount).toBe(2)
  })

  it('empty base messages → empty greenfield tree, not fully reconstructed', () => {
    const result = reconstructTreeWithFans({
      baseMessages: [],
      leaves: [
        mkLeaf({ id: 'a0', treePath: '[["attempt",0]]' }),
        mkLeaf({ id: 'a1', treePath: '[["attempt",1]]' }),
      ],
    })
    // No base messages to seed the spine — fall back, don't crash.
    expect(result.fullyReconstructed).toBe(false)
    expect(result.tree.nodes.some((n) => n.kind === 'fan')).toBe(false)
  })
})
