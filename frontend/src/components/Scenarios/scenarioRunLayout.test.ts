import type {
  ScenarioActiveConversation,
  ScenarioHierarchyGroup,
  ScenarioHierarchyNode,
  ScenarioProgressCounts,
  ScenarioProgressResult,
  ScenarioRunPlan,
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
const RESULT: ScenarioProgressResult = {
  attack_result_id: 'attack', conversation_id: 'main', related_conversation_ids: ['pruned'], atomic_group_id: 'group',
  atomic_attack_name: 'PromptSendingAttack', seed_group_id: 'seed', outcome: 'success', execution_time_ms: 1,
  timestamp: '2026-01-01T00:00:00Z', total_retries: 0, retries: [],
}
const PLAN: ScenarioRunPlan = {
  version: 1,
  atomic_groups: [{
    id: 'group', atomic_attack_name: RESULT.atomic_attack_name, display_group: 'Direct', technique_eval_hash: 'hash',
    seed_group_ids: ['seed'], tags: [], is_compound: false,
  }],
  seed_groups: [{ id: 'seed', objective_sha256: 'sha', objective: 'Objective', prompts: [] }],
}
const PLANNED_GROUP = PLAN.atomic_groups[0]
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
  it('keeps single-card eligibility across saved retry attempts', () => {
    const attempts = [RESULT, { ...RESULT, attack_result_id: 'retry' }]
    expect(singleAtomicGroupId(PLAN)).toBe('group')
    expect(singleAttackAttempt(PLANNED_GROUP, attempts, [hierarchyGroup([root(), root({ attack_result_id: 'retry' })])], [], false))
      .toBe(attempts[1])
  })
  it('should find the only atomic group of a one-attack run', () => {
    expect(singleAtomicGroupId(PLAN)).toBe('group')
  })

  it.each<[string, ScenarioRunPlan | null]>([
    ['an unknown plan', null],
    ['legacy unknown capability', { ...PLAN, atomic_groups: [{ ...PLANNED_GROUP, is_compound: undefined }] }],
    ['a running compound before children exist', { ...PLAN, atomic_groups: [{ ...PLANNED_GROUP, is_compound: true }] }],
    ['no atomic group', { ...PLAN, atomic_groups: [] }],
    ['two atomic groups', { ...PLAN, atomic_groups: [PLANNED_GROUP, { ...PLANNED_GROUP, id: 'other' }] }],
    ['two seeds', { ...PLAN, seed_groups: [...PLAN.seed_groups, { ...PLAN.seed_groups[0], id: 'other' }] }],
  ])('should not treat a run with %s as a single attack', (_label, plan) => {
    expect(singleAtomicGroupId(plan)).toBeNull()
  })
})

describe('singleAttackAttempt', () => {
  it('prefers a matching active retry over the previous saved result', () => {
    const retry = { ...LIVE, atomic_attack_name: RESULT.atomic_attack_name, seed_group_id: RESULT.seed_group_id }
    expect(singleAttackAttempt(PLANNED_GROUP, [RESULT], [], [retry], true)).toBe(retry)
    expect(singleAttackAttempt(PLANNED_GROUP, [RESULT], [], [retry], false)).toBe(RESULT)
  })
  it('uses saved results and hierarchy fallback but never an unrelated active conversation', () => {
    const saved = root()
    expect(singleAttackAttempt(PLANNED_GROUP, [RESULT], [hierarchyGroup([saved])], [LIVE], true)).toBe(RESULT)
    expect(singleAttackAttempt(PLANNED_GROUP, [], [hierarchyGroup([saved])], [LIVE], true)).toBe(saved)
    expect(singleAttackAttempt(PLANNED_GROUP, [], [], [LIVE], true)).toBeNull()
    expect(singleAttackAttempt(PLANNED_GROUP, [], [], [], false)).toBeNull()
  })
  it('chooses the latest saved retry and applies the same chronology to hierarchy roots', () => {
    const latest = { ...RESULT, attack_result_id: 'retry', timestamp: '2026-01-02T00:00:00Z' }
    expect(singleAttackAttempt(PLANNED_GROUP, [RESULT, latest], [], [], false)).toBe(latest)
    const latestRoot = root({ attack_result_id: latest.attack_result_id, timestamp: latest.timestamp })
    expect(singleAttackAttempt(PLANNED_GROUP, [], [hierarchyGroup([latestRoot, root()])], [], false)).toBe(latestRoot)
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
