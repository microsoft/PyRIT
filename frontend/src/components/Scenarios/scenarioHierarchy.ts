import type {
  AttackResultRole,
  ScenarioDisplayGroupProgress,
  ScenarioHierarchyGroup,
  ScenarioHierarchyNode,
  ScenarioHierarchyProjection,
} from '@/types'

/** One saved child-reference position under a parent: a loaded child, or a reference that did not resolve. */
export type HierarchyChildSlot =
  | { readonly position: number; readonly kind: 'child'; readonly node: ScenarioHierarchyNode }
  | { readonly position: number; readonly kind: 'unresolved'; readonly attackResultId: string }

/** A loaded page of a parent's saved child references, in saved order. */
export interface HierarchyChildPage {
  readonly offset: number
  readonly referenceIds: readonly string[]
  readonly children: readonly ScenarioHierarchyNode[]
}

/** Builds a child page from a node returned by either hierarchy endpoint. */
export function childPageFromNode(node: ScenarioHierarchyNode): HierarchyChildPage {
  return {
    offset: node.child_offset,
    referenceIds: node.child_attack_result_ids,
    children: node.children,
  }
}

/**
 * Lays out every saved reference position on a page.
 *
 * Resolved children are matched by their absolute `child_index`, not by id, because a duplicate
 * reference to the same result is unresolved and still occupies its own position.
 */
export function childSlots(page: HierarchyChildPage): HierarchyChildSlot[] {
  const childrenByPosition = new Map<number, ScenarioHierarchyNode>()
  for (const child of page.children) {
    if (child.child_index !== null && child.child_index !== undefined) {
      childrenByPosition.set(child.child_index, child)
    }
  }
  return page.referenceIds.map((attackResultId, i) => {
    const position = page.offset + i
    const node = childrenByPosition.get(position)
    return node ? { position, kind: 'child', node } : { position, kind: 'unresolved', attackResultId }
  })
}

/**
 * Whether the tree preview already holds every saved child reference of a node, so expanding it
 * needs no request.
 */
export function previewHasAllChildren(node: ScenarioHierarchyNode): boolean {
  return (
    !node.children_truncated &&
    !node.child_references_truncated &&
    !node.next_children_cursor &&
    node.child_offset === 0 &&
    (node.children_count === null ||
      node.children_count === undefined ||
      node.children_count === node.child_attack_result_ids.length)
  )
}

/** Whether a node has saved child references at all. */
export function hasChildReferences(node: ScenarioHierarchyNode): boolean {
  return node.child_attack_result_ids.length > 0 || (node.children_count ?? 0) > 0
}

/**
 * Appends a page of roots to the groups already loaded. Groups keep their first-seen order;
 * counts come from the latest page because they are exact run totals, not page totals.
 */
export function mergeRootPage(
  groups: readonly ScenarioHierarchyGroup[],
  page: ScenarioHierarchyProjection,
): ScenarioHierarchyGroup[] {
  const merged = groups.map((group) => ({ ...group, roots: [...group.roots] }))
  const indexById = new Map(merged.map((group, i) => [group.atomic_group_id, i]))
  for (const group of page.groups) {
    const i = indexById.get(group.atomic_group_id)
    if (i === undefined) {
      indexById.set(group.atomic_group_id, merged.length)
      merged.push({ ...group, roots: [...group.roots] })
    } else {
      merged[i] = { ...merged[i], counts: group.counts, roots: [...merged[i].roots, ...group.roots] }
    }
  }
  return merged
}

/** Keeps live display-group totals, adding saved groups absent from the progress summary. */
export function hierarchyDisplayGroups(
  displayGroups: readonly ScenarioDisplayGroupProgress[],
  hierarchyGroups: readonly ScenarioHierarchyGroup[],
): ScenarioDisplayGroupProgress[] {
  const knownIds = new Set(displayGroups.flatMap((group) => group.atomic_group_ids))
  return [
    ...displayGroups,
    ...hierarchyGroups
      .filter((group) => !knownIds.has(group.atomic_group_id))
      .map((group) => ({
        ...group.counts,
        id: `saved:${group.atomic_group_id}`,
        display_group: group.display_group,
        atomic_attack_names: [group.atomic_attack_name],
        atomic_group_ids: [group.atomic_group_id],
      })),
  ]
}

const ROLE_LABELS: Record<AttackResultRole, string> = {
  orchestration: 'Orchestration',
  target_facing: 'Target-facing',
  unknown: 'Unknown role',
}

export function resultRoleLabel(role: AttackResultRole): string {
  return ROLE_LABELS[role] ?? ROLE_LABELS.unknown
}
