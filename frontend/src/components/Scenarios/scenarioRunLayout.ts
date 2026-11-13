import type {
  ScenarioActiveConversation,
  ScenarioHierarchyGroup,
  ScenarioHierarchyNode,
  ScenarioProgressResult,
  ScenarioRunPlan,
  ScenarioRunPlanAtomicGroup,
} from '@/types'
import { compareAttempts, type ScenarioRunProgressState } from '@/utils/scenarioRunProgress'

/** Stable eligibility comes from the saved plan, never the number of attempts saved so far. */
export function singleAtomicGroupId(plan: ScenarioRunPlan | null): string | null {
  const group = plan?.atomic_groups[0]
  return plan?.atomic_groups.length === 1 && plan.seed_groups.length === 1
    && group?.seed_group_ids.length === 1 && group.seed_group_ids[0] === plan.seed_groups[0]?.id
    && group.is_compound === false ? group.id : null
}

/** A matching live retry while active, otherwise the latest applicable saved attempt or hierarchy root. */
export function singleAttackAttempt(
  group: ScenarioRunPlanAtomicGroup,
  results: readonly ScenarioProgressResult[],
  hierarchyGroups: readonly ScenarioHierarchyGroup[],
  activeConversations: readonly ScenarioActiveConversation[],
  active: boolean,
): ScenarioProgressResult | ScenarioHierarchyNode | ScenarioActiveConversation | null {
  const live = active ? activeConversations.find((conversation) =>
    conversation.atomic_attack_name === group.atomic_attack_name
    && conversation.seed_group_id !== null && conversation.seed_group_id !== undefined
    && group.seed_group_ids.includes(conversation.seed_group_id)) : undefined
  return live
    ?? results.filter((attempt) => attempt.atomic_group_id === group.id
      && group.seed_group_ids.includes(attempt.seed_group_id)).slice(-1)[0]
    ?? hierarchyGroups.find((candidate) => candidate.atomic_group_id === group.id)?.roots
      .filter((root) => group.seed_group_ids.includes(root.seed_group_id)).sort(compareAttempts).slice(-1)[0]
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
