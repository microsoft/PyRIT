import type { AttackSummary } from '../../types'

export function isAttackOrchestrationSummary(attackSummary: AttackSummary): boolean {
  return attackSummary.attack_type === 'SequentialAttack'
    && attackSummary.conversation_id.length === 0
    && attackSummary.message_count === 0
}

export function childAttackResultIds(attackSummary: AttackSummary): string[] {
  const childIds = attackSummary.metadata?.child_attack_result_ids
  if (!Array.isArray(childIds)) {
    return []
  }
  return childIds.filter((childId) => typeof childId === 'string' && childId.trim().length > 0)
}
