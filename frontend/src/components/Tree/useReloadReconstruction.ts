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

import {
  backendMessagePieces,
  detectFansV10Plus,
  reconstructTreeWithFans,
  type ConverterResolver,
  type LeafForFanDetection,
} from '../../runner/autoReverse'
import type { ConversationTree, ConversationTreeId } from '../../runner/treeTypes'
import type {
  AttackListResponse,
  AttackSummary,
  ComponentIdentifier,
  ConversationMessagesResponse,
} from '../../types'

export interface ReloadReconstructionApi {
  listAttacks(params?: {
    limit?: number
    cursor?: string
    label?: string[]
  }): Promise<AttackListResponse>
  getMessages(attackResultId: string, conversationId: string): Promise<ConversationMessagesResponse>
}

/** Disclosure payload when slice-1 linear reload drops fan topology. */
export interface ReconstructionDegradedInfo {
  /** Number of fans detected in the AR set that the linear reload could not represent. */
  fanCount: number
  /** Why the reconstruction was degraded. Omitted by older callers. */
  reason?: 'topology' | 'converter_resolution'
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

async function listTreeAttacks(
  reloadApi: ReloadReconstructionApi,
  fragmentTreeId: string,
): Promise<AttackSummary[]> {
  const items: AttackSummary[] = []
  let cursor: string | null | undefined
  do {
    const page = await reloadApi.listAttacks({
      limit: 100,
      label: [`conversation_tree_id:${fragmentTreeId}`],
      ...(cursor ? { cursor } : {}),
    })
    items.push(...page.items)
    cursor = page.pagination.has_more ? page.pagination.next_cursor : null
  } while (cursor)
  return items
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
      const items = await listTreeAttacks(reloadApi, fragmentTreeId)
      if (cancelled) return
      if (items.length === 0) return

      const base = pickBaseAttack(items)
      const msgs = await reloadApi.getMessages(base.attack_result_id, base.conversation_id)
      if (cancelled) return

      const leaves = items.map((ar) => ({
        attack_result_id: ar.attack_result_id,
        labels: ar.labels,
      }))

      // Converter-fan reconstruction (PR7g slice 3) needs each member leaf's
      // first-user-turn converter pipeline. When the leaf set is a single
      // root-level converter fan, pre-fetch each member's messages and build
      // a resolver keyed by AR id. Other shapes (no fan, attempt fan) don't
      // need it; nested/multi-axis still degrade.
      const { converterResolver, onConverterDivergence, divergedSlots, converterResolutionFailed } =
        await buildConverterResolver({ leaves, items, reloadApi })
      if (cancelled) return

      // Fan-aware reconstruction: reconstructTreeWithFans fully rebuilds the
      // no-fan, single root-level attempt-fan, and single root-level
      // converter-fan (with resolver) cases; nested/multi-axis fans fall back
      // to a linear chain with `fullyReconstructed: false` (degraded banner).
      const recon = reconstructTreeWithFans({
        baseMessages: msgs.messages,
        targetRegistryName: base.target?.target_registry_name,
        leaves,
        converterResolver,
        onConverterDivergence,
      })
      const parentId = pickParentConversationTreeId(items)
      const next: ConversationTree = {
        ...recon.tree,
        id: fragmentTreeId as ConversationTreeId,
        parentConversationTreeId: parentId,
      }
      onTreeChange?.(next)

      // Disclose any topology gap: a fan set that did NOT fully reconstruct
      // (nested/multi-axis, or a converter fan we couldn't resolve) fell back
      // to a linear chain. Surface it rather than silently degrading (PR7
      // review must-fix #3). Fully-reconstructed cases skip the banner.
      if (!recon.fullyReconstructed && recon.fanCount > 0) {
        console.warn(
          `useReloadReconstruction: reconstructed tree '${fragmentTreeId}' as a linear chain; ` +
            `${recon.fanCount} fan(s) in the saved tree are not shown (nested / multi-axis ` +
            `fan-aware reload is deferred)`,
        )
        onReconstructionDegraded?.({ fanCount: recon.fanCount, reason: 'topology' })
      } else if (recon.fullyReconstructed && divergedSlots.length > 0) {
        // Reconstructed, but some converter slots had disagreeing leaves.
        console.warn(
          `useReloadReconstruction: converter fan in tree '${fragmentTreeId}' had ` +
            `${divergedSlots.length} slot(s) where member leaves disagreed on the converter ` +
            `pipeline; showing the most-frequent value per slot`,
        )
      }
      if (recon.fullyReconstructed && converterResolutionFailed) {
        console.warn(
          `useReloadReconstruction: converter fan in tree '${fragmentTreeId}' had ` +
            `one or more member leaves whose converter pipeline could not be fetched; ` +
            `showing those slots without converters`,
        )
        onReconstructionDegraded?.({ fanCount: 1, reason: 'converter_resolution' })
      }
    })().catch(() => {
      // Fail soft on reload reconstruction errors.
    })
    return () => {
      cancelled = true
    }
  }, [currentTree, fragmentTreeId, onReconstructionDegraded, onTreeChange, reloadApi])
}

/**
 * If the leaf set is a single root-level `converter` fan, pre-fetch each
 * member leaf's messages and build a resolver over its first user-turn's
 * `converter_identifiers`. Returns an undefined resolver otherwise (so
 * non-converter shapes skip the N fetches).
 */
async function buildConverterResolver(args: {
  leaves: LeafForFanDetection[]
  items: AttackSummary[]
  reloadApi: ReloadReconstructionApi
}): Promise<{
  converterResolver: ConverterResolver | undefined
  onConverterDivergence: (slotIndex: number) => void
  divergedSlots: number[]
  converterResolutionFailed: boolean
}> {
  const divergedSlots: number[] = []
  let converterResolutionFailed = false
  const onConverterDivergence = (slot: number): void => {
    divergedSlots.push(slot)
  }
  const fans = detectFansV10Plus(args.leaves)
  const single = fans.length === 1 ? fans[0] : null
  if (single === null || single.parent_path.length !== 0 || single.axis !== 'converter') {
    return {
      converterResolver: undefined,
      onConverterDivergence,
      divergedSlots,
      converterResolutionFailed,
    }
  }

  const convIdByArId = new Map(args.items.map((it) => [it.attack_result_id, it.conversation_id]))
  const convertersByArId = new Map<string, ComponentIdentifier[]>()
  const results = await Promise.allSettled(
    single.member_ars.map(async (leaf) => {
      const conversationId = convIdByArId.get(leaf.attack_result_id)
      if (conversationId === undefined) {
        converterResolutionFailed = true
        convertersByArId.set(leaf.attack_result_id, [])
        return
      }
      const m = await args.reloadApi.getMessages(leaf.attack_result_id, conversationId)
      const firstUser = m.messages
        .slice()
        .sort((a, b) => a.turn_number - b.turn_number)
        .find((msg) => msg.role === 'user')
      if (firstUser === undefined) converterResolutionFailed = true
      convertersByArId.set(leaf.attack_result_id, firstUser ? backendMessagePieces(firstUser)[0]?.converter_identifiers ?? [] : [])
    }),
  )
  results.forEach((result, index) => {
    if (result.status === 'fulfilled') return
    converterResolutionFailed = true
    convertersByArId.set(single.member_ars[index].attack_result_id, [])
  })
  const converterResolver: ConverterResolver = (leaf) =>
    convertersByArId.get(leaf.attack_result_id) ?? []
  return { converterResolver, onConverterDivergence, divergedSlots, converterResolutionFailed }
}
