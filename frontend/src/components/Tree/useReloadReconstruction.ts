// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Hook that reconstructs a tree from URL-fragment conversation_tree_id
 * on reload (PR7g first slice).
 *
 * Flow:
 * 1. If fragment id is absent, no-op.
 * 2. Query attacks list filtered by `conversation_tree_id:<id>`.
 * 3. Pick a base AR (oldest created_at) and fetch its messages.
 * 4. Build a linear tree via `linearChainFromMessages`.
 * 5. Force tree.id to fragment id and hoist
 *    `labels.parent_conversation_tree_id` into
 *    `tree.parentConversationTreeId`.
 * 6. Emit via `onTreeChange`.
 *
 * Fan reconstruction from the full AR set is handled in PR7g follow-up.
 */

import { useEffect } from 'react'

import { linearChainFromMessages } from '../../runner/autoReverse'
import type { ConversationTree, ConversationTreeId } from '../../runner/treeTypes'
import type { AttackListResponse, AttackSummary, ConversationMessagesResponse } from '../../types'

export interface ReloadReconstructionApi {
  listAttacks(params?: {
    limit?: number
    label?: string[]
  }): Promise<AttackListResponse>
  getMessages(attackResultId: string, conversationId: string): Promise<ConversationMessagesResponse>
}

export interface UseReloadReconstructionArgs {
  fragmentTreeId: string | null
  currentTree: ConversationTree | null
  onTreeChange?: (tree: ConversationTree) => void
  reloadApi: ReloadReconstructionApi
}

function pickBaseAttack(items: AttackSummary[]): AttackSummary {
  const sorted = [...items].sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at))
  return sorted[0]
}

function pickParentConversationTreeId(items: AttackSummary[]): ConversationTreeId | null {
  const counts = new Map<string, number>()
  for (const ar of items) {
    const parent = ar.labels?.parent_conversation_tree_id
    if (!parent) continue
    counts.set(parent, (counts.get(parent) ?? 0) + 1)
  }
  if (counts.size === 0) return null
  let winner: string | null = null
  let winnerCount = -1
  for (const [id, count] of counts.entries()) {
    if (count > winnerCount) {
      winner = id
      winnerCount = count
    }
  }
  return winner as ConversationTreeId
}

export function useReloadReconstruction({
  fragmentTreeId,
  currentTree,
  onTreeChange,
  reloadApi,
}: UseReloadReconstructionArgs): void {
  useEffect(() => {
    if (fragmentTreeId === null) return
    if (currentTree !== null && currentTree.id === fragmentTreeId) return
    let cancelled = false
    ;(async () => {
      const list = await reloadApi.listAttacks({
        limit: 200,
        label: [`conversation_tree_id:${fragmentTreeId}`],
      })
      if (cancelled) return
      if (list.items.length === 0) return

      const base = pickBaseAttack(list.items)
      const msgs = await reloadApi.getMessages(base.attack_result_id, base.conversation_id)
      if (cancelled) return

      const reconstructed = linearChainFromMessages(msgs.messages)
      const parentId = pickParentConversationTreeId(list.items)
      const next: ConversationTree = {
        ...reconstructed,
        id: fragmentTreeId as ConversationTreeId,
        parentConversationTreeId: parentId,
      }
      onTreeChange?.(next)
    })().catch(() => {
      // Fail soft on reload reconstruction errors.
    })
    return () => {
      cancelled = true
    }
  }, [currentTree, fragmentTreeId, onTreeChange, reloadApi])
}
