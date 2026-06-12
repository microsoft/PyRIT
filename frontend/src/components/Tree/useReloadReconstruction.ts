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

import { reconstructTreeWithFans } from '../../runner/autoReverse'
import type { ConversationTree, ConversationTreeId } from '../../runner/treeTypes'
import type { AttackListResponse, AttackSummary, ConversationMessagesResponse } from '../../types'

export interface ReloadReconstructionApi {
  listAttacks(params?: {
    limit?: number
    label?: string[]
  }): Promise<AttackListResponse>
  getMessages(attackResultId: string, conversationId: string): Promise<ConversationMessagesResponse>
}

/** Disclosure payload when slice-1 linear reload drops fan topology. */
export interface ReconstructionDegradedInfo {
  /** Number of fans detected in the AR set that the linear reload could not represent. */
  fanCount: number
}

export interface UseReloadReconstructionArgs {
  fragmentTreeId: string | null
  currentTree: ConversationTree | null
  onTreeChange?: (tree: ConversationTree) => void
  /**
   * Fired when the AR set has fan topology that slice-1's linear-only
   * reconstruction cannot represent. The host surfaces an operator banner
   * ("reconstructed as a linear chain; some fan structure isn't shown").
   * Until PR7g slice 2 lands fan-aware reload, this is the honest
   * disclosure of the §9.4.1 "fan structure survives reload" gap.
   */
  onReconstructionDegraded?: (info: ReconstructionDegradedInfo) => void
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
  onReconstructionDegraded,
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

      // Fan-aware reconstruction (PR7g slice 2): reconstructTreeWithFans
      // fully rebuilds the no-fan and single root-level attempt-fan cases;
      // converter/nested/multi-axis fans fall back to a linear chain and
      // report `fullyReconstructed: false` so we surface the degraded banner.
      const leaves = list.items.map((ar) => ({
        attack_result_id: ar.attack_result_id,
        labels: ar.labels,
      }))
      const recon = reconstructTreeWithFans({ baseMessages: msgs.messages, leaves })
      const parentId = pickParentConversationTreeId(list.items)
      const next: ConversationTree = {
        ...recon.tree,
        id: fragmentTreeId as ConversationTreeId,
        parentConversationTreeId: parentId,
      }
      onTreeChange?.(next)

      // Disclose any topology gap: a fan set that did NOT fully reconstruct
      // (converter/nested/multi-axis) fell back to a linear chain. Surface it
      // rather than silently degrading (PR7 review must-fix #3). The handled
      // cases (no fans, single root-level attempt fan) report
      // fullyReconstructed=true and skip the banner.
      if (!recon.fullyReconstructed && recon.fanCount > 0) {
        console.warn(
          `useReloadReconstruction: reconstructed tree '${fragmentTreeId}' as a linear chain; ` +
            `${recon.fanCount} fan(s) in the saved tree are not shown (converter/nested fan-aware ` +
            `reload is deferred)`,
        )
        onReconstructionDegraded?.({ fanCount: recon.fanCount })
      }
    })().catch(() => {
      // Fail soft on reload reconstruction errors.
    })
    return () => {
      cancelled = true
    }
  }, [currentTree, fragmentTreeId, onReconstructionDegraded, onTreeChange, reloadApi])
}
