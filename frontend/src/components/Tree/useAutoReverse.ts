// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * `useAutoReverse(attackResultId, deps)` — React hook that owns the
 * AR → ConversationTree reconstruction lifecycle per spec 01 §9.3 +
 * §13.1 `openTreeFromAttackResult`.
 *
 * Single-AR path (V1.0): fetch the AR for its conversation_id, then
 * fetch that conversation's messages, then run PR7a's
 * `linearChainFromMessages`. The result feeds into TreeRunnerHost's
 * `tree` prop.
 *
 * Multi-leaf AR-list path (with `detectFansV10Plus`) is the reload
 * surface in PR7g; that path queries `GET /api/attacks?labels.conversation_tree_id=X`
 * and runs the §9.3.1 fan-detection algorithm.
 *
 * Stale-response handling: a request id (per-effect counter) is
 * captured at fetch start and checked before applying results.
 * Mid-fetch attackResultId changes invalidate prior requests; the
 * effect's cleanup also bumps the request id so unmounts drop
 * in-flight responses.
 */

import { useEffect, useState } from 'react'

import { linearChainFromMessages, mergedTreeFromConversations } from '../../runner/autoReverse'
import type {
  AttackConversationsResponse,
  AttackSummary,
  ConversationMessagesResponse,
} from '../../types'
import type { ConversationTree } from '../../runner/treeTypes'

export interface UseAutoReverseApi {
  getAttack(attackResultId: string): Promise<AttackSummary>
  getMessages(attackResultId: string, conversationId: string): Promise<ConversationMessagesResponse>
  getConversations?(attackResultId: string): Promise<AttackConversationsResponse>
}

export interface UseAutoReverseDeps {
  attacksApi: UseAutoReverseApi
}

export interface UseAutoReverseResult {
  tree: ConversationTree | null
  loading: boolean
  error: Error | null
}

export function useAutoReverse(
  attackResultId: string | null,
  deps: UseAutoReverseDeps,
): UseAutoReverseResult {
  // Track which arId the last fetch resolved for so we can derive
  // loading / tree / error correctly without resetting state in the
  // null-id branch (the react-hooks/set-state-in-effect rule rejects
  // synchronous setState in effect bodies).
  interface FetchedState {
    forArId: string | null
    tree: ConversationTree | null
    error: Error | null
  }
  const [fetched, setFetched] = useState<FetchedState>({
    forArId: null,
    tree: null,
    error: null,
  })

  useEffect(() => {
    if (attackResultId === null) return
    let cancelled = false
    ;(async () => {
      try {
        const ar = await deps.attacksApi.getAttack(attackResultId)
        if (cancelled) return
        const conversationIds = await activeConversationIdsForAttack(attackResultId, ar, deps.attacksApi)
        if (cancelled) return
        const messageResponses = await Promise.all(
          conversationIds.map((conversationId) => deps.attacksApi.getMessages(attackResultId, conversationId)),
        )
        if (cancelled) return
        const built = messageResponses.length > 1
          ? mergedTreeFromConversations(
              messageResponses.map((resp) => ({
                conversation_id: resp.conversation_id,
                messages: resp.messages,
              })),
              { targetRegistryName: ar.target?.target_registry_name },
            ).tree
          : linearChainFromMessages(messageResponses[0]?.messages ?? [], {
              targetRegistryName: ar.target?.target_registry_name,
            })
        // Per spec §13.1: a V1.0+ AR carries `conversation_tree_id`; the
        // opened tree must keep that id so reload/refresh stay consistent.
        // A pre-V1.0 AR (no label) keeps the freshly-minted id.
        const existingTreeId = ar.labels?.conversation_tree_id
        const next =
          existingTreeId !== undefined && existingTreeId !== ''
            ? { ...built, id: existingTreeId as ConversationTree['id'] }
            : built
        setFetched({ forArId: attackResultId, tree: next, error: null })
      } catch (e) {
        if (cancelled) return
        setFetched({
          forArId: attackResultId,
          tree: null,
          error: e instanceof Error ? e : new Error(String(e)),
        })
      }
    })()
    return () => {
      cancelled = true
    }
    // deps.attacksApi is the caller's API surface — V1.0 tests pass a
    // stable mock; production passes the module-level attacksApi which
    // is stable. We intentionally don't track it as a dep to avoid
    // re-fetching on every render if the parent constructs a fresh
    // wrapper object each time.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attackResultId])

  if (attackResultId === null) {
    return { tree: null, loading: false, error: null }
  }
  const inSync = fetched.forArId === attackResultId
  return {
    tree: inSync ? fetched.tree : null,
    loading: !inSync,
    error: inSync ? fetched.error : null,
  }
}

async function activeConversationIdsForAttack(
  attackResultId: string,
  ar: AttackSummary,
  api: UseAutoReverseApi,
): Promise<string[]> {
  if (api.getConversations === undefined) return [ar.conversation_id]
  const response = await api.getConversations(attackResultId)
  const ids = [
    response.main_conversation_id || ar.conversation_id,
    ...response.conversations.map((conversation) => conversation.conversation_id),
    ar.conversation_id,
  ]
  return Array.from(new Set(ids.filter((id) => id !== undefined && id !== '')))
}
