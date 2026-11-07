import type { FindingPath } from '@/types'

/** The fields that place a finding in its Operation's decomposition trees. */
interface FindingNode {
  readonly id: string
  readonly parent_finding_id?: string | null
  readonly created_at: string
}

/** A finding with how far below a listed root it sits. */
export interface FindingTreeRow<T extends FindingNode> {
  readonly finding: T
  readonly depth: number
}

/** Oldest first, the order a decomposition is usually written in, with the ID keeping ties stable. */
function bySubFindingOrder(first: FindingNode, second: FindingNode): number {
  const created = Date.parse(first.created_at) - Date.parse(second.created_at)
  if (created !== 0) return created
  return first.id < second.id ? -1 : first.id > second.id ? 1 : 0
}

/**
 * The direct sub-findings of each finding, oldest first, keyed by parent. Findings whose parent is not listed are
 * roots, keyed by null and kept in listing order.
 */
export function findingChildren<T extends FindingNode>(findings: readonly T[]): Map<string | null, T[]> {
  const listed = new Set(findings.map(finding => finding.id))
  const children = new Map<string | null, T[]>()
  for (const finding of findings) {
    const parentId = finding.parent_finding_id && listed.has(finding.parent_finding_id) ? finding.parent_finding_id : null
    const siblings = children.get(parentId)
    if (siblings) siblings.push(finding)
    else children.set(parentId, [finding])
  }
  for (const [parentId, siblings] of children) {
    if (parentId !== null) siblings.sort(bySubFindingOrder)
  }
  return children
}

/** Findings depth-first, each parent before its sub-findings. */
export function flattenFindingTree<T extends FindingNode>(findings: readonly T[]): Array<FindingTreeRow<T>> {
  const children = findingChildren(findings)
  const rows: Array<FindingTreeRow<T>> = []
  // Without recursion, so a deep decomposition cannot exhaust the stack.
  const pending: Array<FindingTreeRow<T>> = [...(children.get(null) ?? [])].reverse()
    .map(finding => ({ finding, depth: 0 }))
  while (pending.length > 0) {
    const row = pending.pop() as FindingTreeRow<T>
    rows.push(row)
    const below = [...(children.get(row.finding.id) ?? [])].reverse()
    pending.push(...below.map(finding => ({ finding, depth: row.depth + 1 })))
  }
  return rows
}

/** Every finding below one finding, at any depth. */
export function findingDescendantIds<T extends FindingNode>(
  children: ReadonlyMap<string | null, readonly T[]>, findingId: string,
): string[] {
  const descendants: string[] = []
  const pending = [findingId]
  while (pending.length > 0) {
    const current = pending.pop() as string
    for (const child of children.get(current) ?? []) {
      descendants.push(child.id)
      pending.push(child.id)
    }
  }
  return descendants
}

/** Whether every step of a path is a demonstrated sub-finding. Unknown steps count as not demonstrated. */
export function isFindingPathComplete(
  path: Pick<FindingPath, 'steps'>, demonstrated: ReadonlyMap<string, boolean>,
): boolean {
  return path.steps.length > 0 && path.steps.every(step => demonstrated.get(step) === true)
}

/** A path's name, or its position when it has none. */
export function findingPathName(path: Pick<FindingPath, 'name'>, index: number): string {
  return path.name?.trim() || `Path ${index + 1}`
}
