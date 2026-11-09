import type {
  ScenarioActiveConversation,
  ScenarioHierarchyGroup,
  ScenarioHierarchyNode,
  ScenarioProgressCounts,
  ScenarioProgressResult,
  ScenarioProgressSummary,
} from '@/types'
import { INITIAL_SCENARIO_RUN_PROGRESS_STATE, type ScenarioRunProgressState } from '@/utils/scenarioRunProgress'

import {
  attackIdForConversation,
  interruptedConversations,
  liveAttacks,
  singleAtomicGroupId,
  singleAttackAttempt,
} from './scenarioRunLayout'

const COUNTS: ScenarioProgressCounts = { completed: 0, planned: 1, succeeded: 0, success_percentage: null, errors: 0, retries: 0 }
const GROUP = { ...COUNTS, id: 'direct', display_group: 'Direct', atomic_attack_names: ['PromptSendingAttack'], atomic_group_ids: ['group'] }
const SUMMARY: ScenarioProgressSummary = {
  overall: COUNTS, display_groups: [GROUP], techniques: [], seed_groups: [], atomic_groups: [],
}
const RESULT: ScenarioProgressResult = {
  attack_result_id: 'attack', conversation_id: 'main', related_conversation_ids: ['pruned'], atomic_group_id: 'group',
  atomic_attack_name: 'PromptSendingAttack', seed_group_id: 'seed', outcome: 'success', execution_time_ms: 1,
  timestamp: '2026-01-01T00:00:00Z', total_retries: 0, retries: [],
}
const LIVE: ScenarioActiveConversation = {
  attack_result_id: 'live', conversation_id: 'live-main', conversation_ids: ['live-first', 'live-main'],
  atomic_attack_name: 'Crescendo', objective: 'Objective', started_at: '2026-01-01T00:00:00Z', seed_identifiers: [],
}

function root(overrides: Partial<ScenarioHierarchyNode> = {}): ScenarioHierarchyNode {
  return {
    attack_result_id: 'root', conversation_id: 'root-main', result_role: 'target_facing', atomic_attack_name: 'PromptSendingAttack',
    atomic_group_id: 'group', seed_group_id: 'seed', objective: 'Objective', outcome: 'success', execution_time_ms: 1,
    executed_turns: 1, timestamp: '2026-01-01T00:00:00Z', total_retries: 0, retries: [], retries_truncated: false,
    child_attack_result_ids: [], child_references_truncated: false, children: [], children_truncated: false,
    child_offset: 0, unresolved_child_attack_result_ids: [], unresolved_child_references_truncated: false,
    conversations: [], conversations_truncated: false, ...overrides,
  }
}

function hierarchyGroup(roots: ScenarioHierarchyNode[]): ScenarioHierarchyGroup {
  return { atomic_group_id: 'group', atomic_attack_name: 'PromptSendingAttack', display_group: 'Direct', roots, counts: COUNTS }
}

function state(overrides: Partial<ScenarioRunProgressState>): ScenarioRunProgressState {
  return { ...INITIAL_SCENARIO_RUN_PROGRESS_STATE, ...overrides }
}

describe('singleAtomicGroupId', () => {
  it('should find the only atomic group of a one-attack run', () => {
    expect(singleAtomicGroupId(SUMMARY, [], 0)).toBe('group')
    expect(singleAtomicGroupId(SUMMARY, [hierarchyGroup([root()])], 1)).toBe('group')
  })

  it.each([
    ['more than one planned attack', { ...SUMMARY, overall: { ...COUNTS, planned: 2 } }, [], 0],
    ['an unknown plan', { ...SUMMARY, overall: { ...COUNTS, planned: null } }, [], 0],
    ['more than one execution', SUMMARY, [], 2],
    ['no display groups', { ...SUMMARY, display_groups: [] }, [], 0],
    ['no atomic group', { ...SUMMARY, display_groups: [{ ...GROUP, atomic_group_ids: [] }] }, [], 0],
    ['two atomic groups', { ...SUMMARY, display_groups: [{ ...GROUP, atomic_group_ids: ['group', 'other'] }] }, [], 0],
    ['two display groups', { ...SUMMARY, display_groups: [GROUP, { ...GROUP, id: 'other', atomic_group_ids: ['other'] }] }, [], 0],
    ['two saved roots', SUMMARY, [hierarchyGroup([root(), root({ attack_result_id: 'other' })])], 1],
    ['a compound root', SUMMARY, [hierarchyGroup([root({ child_attack_result_ids: ['child'] })])], 1],
  ] as const)('should not treat a run with %s as a single attack', (_label, summary, groups, loaded) => {
    expect(singleAtomicGroupId(summary, groups, loaded)).toBeNull()
  })
})

describe('singleAttackAttempt', () => {
  it('should prefer the saved result, then the saved root, then the running attack', () => {
    const saved = root()
    expect(singleAttackAttempt('group', [RESULT], [hierarchyGroup([saved])], [LIVE])).toBe(RESULT)
    expect(singleAttackAttempt('group', [], [hierarchyGroup([saved])], [LIVE])).toBe(saved)
    expect(singleAttackAttempt('group', [], [], [LIVE])).toBe(LIVE)
    expect(singleAttackAttempt('group', [], [], [])).toBeNull()
  })
})

describe('liveAttacks', () => {
  it('should have no running attacks when no conversations are active', () => {
    expect(liveAttacks([])).toEqual([])
  })

  it('should list each running attack once with all of its conversations counted', () => {
    const attacks = liveAttacks([LIVE, { ...LIVE, conversation_id: 'live-first', conversation_ids: undefined }, { ...LIVE, attack_result_id: 'other', conversation_ids: undefined }])
    expect(attacks.map((attack) => [attack.conversation.attack_result_id, attack.conversationCount])).toEqual([['live', 2], ['other', 1]])
  })
})

describe('interruptedConversations', () => {
  it('should keep saved conversations that no running or completed attack shows, once each', () => {
    const recorded = { ...LIVE, attack_result_id: 'stopped', conversation_id: 'stopped-main', conversation_ids: undefined }
    const conversations = interruptedConversations(state({
      results: [RESULT], activeConversations: [LIVE],
      recordedConversations: [recorded, recorded, { ...recorded, conversation_id: 'pruned' }, { ...recorded, conversation_id: 'live-first' }],
    }))
    expect(conversations).toEqual([recorded])
  })
})

describe('attackIdForConversation', () => {
  it('should find the attack owning a completed, pruned, running, or interrupted conversation', () => {
    const run = state({
      results: [RESULT], activeConversations: [LIVE],
      recordedConversations: [{ ...LIVE, attack_result_id: 'stopped', conversation_id: 'stopped-main' }],
    })
    expect(attackIdForConversation(run, 'main')).toBe('attack')
    expect(attackIdForConversation(run, 'pruned')).toBe('attack')
    expect(attackIdForConversation(run, 'live-first')).toBe('live')
    expect(attackIdForConversation(run, 'stopped-main')).toBe('stopped')
    expect(attackIdForConversation(run, 'unknown')).toBeNull()
  })
})
