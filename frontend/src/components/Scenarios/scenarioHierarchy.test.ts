import type { ScenarioHierarchyGroup, ScenarioHierarchyNode, ScenarioHierarchyProjection } from '@/types'

import { childSlots, mergeRootPage, previewHasAllChildren } from './scenarioHierarchy'

function node(overrides: Partial<ScenarioHierarchyNode> = {}): ScenarioHierarchyNode {
  return {
    attack_result_id: 'ar',
    conversation_id: '',
    result_role: 'target_facing',
    atomic_attack_name: 'attack',
    atomic_group_id: 'g1',
    seed_group_id: 's1',
    objective: 'objective',
    outcome: 'success',
    execution_time_ms: 0,
    executed_turns: 0,
    timestamp: '2026-10-28T00:00:00Z',
    total_retries: 0,
    retries: [],
    retries_truncated: false,
    child_attack_result_ids: [],
    child_references_truncated: false,
    children: [],
    children_truncated: false,
    child_offset: 0,
    unresolved_child_attack_result_ids: [],
    unresolved_child_references_truncated: false,
    conversations: [],
    conversations_truncated: false,
    ...overrides,
  }
}

const counts = (completed: number) => ({
  completed,
  planned: 10,
  succeeded: 0,
  success_percentage: null,
  errors: 0,
  retries: 0,
})

function group(id: string, roots: string[], completed: number): ScenarioHierarchyGroup {
  return {
    atomic_group_id: id,
    atomic_attack_name: id,
    display_group: id,
    roots: roots.map((rootId) => node({ attack_result_id: rootId, atomic_group_id: id })),
    counts: counts(completed),
  }
}

describe('childSlots', () => {
  it('keeps every saved position, including unresolved and duplicate references', () => {
    // a resolves at 200; 'missing' never resolves; the second 'a' is a duplicate and stays unresolved
    const slots = childSlots({
      offset: 200,
      referenceIds: ['a', 'missing', 'a'],
      children: [node({ attack_result_id: 'a', child_index: 200 })],
    })

    expect(slots.map((slot) => [slot.position, slot.kind])).toEqual([
      [200, 'child'],
      [201, 'unresolved'],
      [202, 'unresolved'],
    ])
    expect(slots[2]).toMatchObject({ kind: 'unresolved', attackResultId: 'a' })
  })

  it('lays out a page where no reference resolves', () => {
    const slots = childSlots({ offset: 0, referenceIds: ['x', 'y'], children: [] })

    expect(slots.every((slot) => slot.kind === 'unresolved')).toBe(true)
    expect(slots.map((slot) => slot.position)).toEqual([0, 1])
  })
})

describe('mergeRootPage', () => {
  it('appends roots to known groups and keeps the latest exact counts', () => {
    const first = mergeRootPage([], { groups: [group('g1', ['r1'], 3)] } as ScenarioHierarchyProjection)
    const merged = mergeRootPage(first, {
      groups: [group('g1', ['r2'], 3), group('g2', ['r3'], 1)],
    } as ScenarioHierarchyProjection)

    expect(merged.map((g) => [g.atomic_group_id, g.roots.map((r) => r.attack_result_id)])).toEqual([
      ['g1', ['r1', 'r2']],
      ['g2', ['r3']],
    ])
    expect(merged[0].counts.completed).toBe(3)
    expect(first[0].roots).toHaveLength(1)
  })
})

describe('previewHasAllChildren', () => {
  it('needs a request when the preview deferred or paged the children', () => {
    expect(previewHasAllChildren(node({ child_attack_result_ids: ['a'], children_count: 1 }))).toBe(true)
    expect(previewHasAllChildren(node({ child_attack_result_ids: ['a'], children_truncated: true }))).toBe(false)
    expect(previewHasAllChildren(node({ child_attack_result_ids: ['a'], next_children_cursor: 'c' }))).toBe(false)
    expect(previewHasAllChildren(node({ child_attack_result_ids: ['a'], children_count: 3 }))).toBe(false)
  })
})
