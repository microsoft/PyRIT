import type {
  ScenarioActiveConversation,
  ScenarioHierarchyGroup,
  ScenarioHierarchyNode,
  ScenarioProgressResult,
  ScenarioProgressSummary,
} from '@/types'
import type { ScenarioRunProgressState } from '@/utils/scenarioRunProgress'

import { hasChildReferences, hierarchyDisplayGroups, runExecutionCount } from './scenarioHierarchy'

/**
 * The atomic group of a run made of exactly one atomic attack: one planned attack, at most one execution,
 * one display group with one atomic group, and no compound children. Null for any other run.
 */
export function singleAtomicGroupId(
  summary: ScenarioProgressSummary,
  hierarchyGroups: readonly ScenarioHierarchyGroup[],
  loadedResults: number,
): string | null {
  const displayGroups = hierarchyDisplayGroups(summary.display_groups ?? summary.techniques, hierarchyGroups)
  const roots = hierarchyGroups.flatMap((group) => group.roots)
  const displayGroup = displayGroups[0]
  const root = roots[0]
  const single = summary.overall.planned === 1
    && runExecutionCount(summary, loadedResults).count <= 1
    && displayGroups.length === 1
    && displayGroup?.atomic_group_ids.length === 1
    && roots.length <= 1
    && (!root || !hasChildReferences(root))
  return single ? displayGroup?.atomic_group_ids[0] ?? null : null
}

/** The single attack's attempt: its saved result, otherwise its saved root, otherwise the running attack. */
export function singleAttackAttempt(
  atomicGroupId: string,
  results: readonly ScenarioProgressResult[],
  hierarchyGroups: readonly ScenarioHierarchyGroup[],
  activeConversations: readonly ScenarioActiveConversation[],
): ScenarioProgressResult | ScenarioHierarchyNode | ScenarioActiveConversation | null {
  return results.find((attempt) => attempt.atomic_group_id === atomicGroupId)
    ?? hierarchyGroups.find((group) => group.atomic_group_id === atomicGroupId)?.roots[0]
    ?? activeConversations[0]
    ?? null
}

/** One running attack, which may have started several objective conversations. */
export interface LiveAttack {
  /** The conversation the attack is working on. */
  readonly conversation: ScenarioActiveConversation
  readonly conversationCount: number
}

/** Running attacks, one per attack, in the order they were reported. */
export function liveAttacks(activeConversations: readonly ScenarioActiveConversation[]): LiveAttack[] {
  const conversationsByAttack = new Map<string, [ScenarioActiveConversation, ...ScenarioActiveConversation[]]>()
  for (const conversation of activeConversations) {
    const conversations = conversationsByAttack.get(conversation.attack_result_id)
    if (conversations) {
      conversations.push(conversation)
    } else {
      conversationsByAttack.set(conversation.attack_result_id, [conversation])
    }
  }
  return [...conversationsByAttack.values()].map((conversations) => ({
    conversation: conversations[0],
    conversationCount: new Set(conversations.flatMap((conversation) => [
      conversation.conversation_id, ...(conversation.conversation_ids ?? []),
    ])).size,
  }))
}

/** Conversations saved by attacks that stopped before saving a result, excluding running and completed ones. */
export function interruptedConversations(state: ScenarioRunProgressState): ScenarioActiveConversation[] {
  const shownConversationIds = new Set([
    ...state.results.flatMap((attempt) => [attempt.conversation_id, ...(attempt.related_conversation_ids ?? [])]),
    ...state.activeConversations.flatMap((conversation) => [
      conversation.conversation_id, ...(conversation.conversation_ids ?? []),
    ]),
  ])
  const interrupted = new Map<string, ScenarioActiveConversation>()
  for (const conversation of state.recordedConversations) {
    if (!shownConversationIds.has(conversation.conversation_id)) {
      interrupted.set(`${conversation.attack_result_id}:${conversation.conversation_id}`, conversation)
    }
  }
  return [...interrupted.values()]
}

/** The attack in a run that owns a conversation, whether it is running, completed, or was interrupted. */
export function attackIdForConversation(state: ScenarioRunProgressState, conversationId: string): string | null {
  const active = state.activeConversations.find((conversation) =>
    conversation.conversation_id === conversationId || conversation.conversation_ids?.includes(conversationId))
  const completed = state.results.find((attempt) =>
    attempt.conversation_id === conversationId || attempt.related_conversation_ids?.includes(conversationId))
  const recorded = state.recordedConversations.find((conversation) => conversation.conversation_id === conversationId)
  return completed?.attack_result_id ?? active?.attack_result_id ?? recorded?.attack_result_id ?? null
}
