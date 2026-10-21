import { useCallback, useState } from 'react'

import { attacksApi, scoresApi } from '@/services/api'
import type { AttackOutcome, AttackSummary, BackendScore } from '@/types'

export interface UseAttackMutationsOptions {
  attackResultId: string | null
  activeConversationId: string | null
  /** The loaded attack's objective. */
  objective: string
  lastResponseMessagePieceId?: string | null
  humanScore?: BackendScore | null
  isScoreLocked: boolean
  loadConversation: (attackResultId: string, conversationId: string) => Promise<void>
  getViewedConversationId: () => string | null
  onHumanScoreChange?: (score: BackendScore | null, outcome: AttackOutcome) => void
  onAttackChange?: (attack: AttackSummary) => void
  onObjectiveChange?: (objective: string) => void
}

export interface AttackMutations {
  /** Objective typed before the first send creates the attack. */
  pendingObjective: string
  currentObjective: string
  /** Clears the pending objective when the attack is reset; safe to call while rendering. */
  resetPendingObjective: () => void
  updateHumanScore: (value: boolean, rationale: string) => Promise<void>
  removeHumanScore: () => Promise<void>
  changeObjective: (objective: string, expectedObjective: string) => Promise<void>
}

export function useAttackMutations({
  attackResultId,
  activeConversationId,
  objective,
  lastResponseMessagePieceId,
  humanScore,
  isScoreLocked,
  loadConversation,
  getViewedConversationId,
  onHumanScoreChange,
  onAttackChange,
  onObjectiveChange,
}: UseAttackMutationsOptions): AttackMutations {
  const [pendingObjective, setPendingObjective] = useState('')
  const currentObjective = attackResultId ? objective : pendingObjective

  const resetPendingObjective = useCallback((): void => { setPendingObjective('') }, [])

  const updateHumanScore = useCallback(async (value: boolean, rationale: string): Promise<void> => {
    if (
      !attackResultId
      || !lastResponseMessagePieceId
      || !currentObjective.trim()
      || isScoreLocked
    ) {
      return
    }

    const score = await scoresApi.createManualScore({
      attack_result_id: attackResultId,
      message_id: lastResponseMessagePieceId,
      value,
      rationale,
      update_attack: true,
    })
    onHumanScoreChange?.(score, value ? 'success' : 'failure')
    if (activeConversationId && getViewedConversationId() === activeConversationId) {
      await loadConversation(attackResultId, activeConversationId)
    }
  }, [
    activeConversationId,
    attackResultId,
    getViewedConversationId,
    isScoreLocked,
    lastResponseMessagePieceId,
    loadConversation,
    onHumanScoreChange,
    currentObjective,
  ])

  const removeHumanScore = useCallback(async (): Promise<void> => {
    if (!attackResultId || !humanScore || isScoreLocked) return

    const attack = await attacksApi.removeHumanScore(attackResultId)
    onHumanScoreChange?.(null, attack.outcome ?? 'undetermined')
    if (activeConversationId && getViewedConversationId() === activeConversationId) {
      await loadConversation(attackResultId, activeConversationId)
    }
  }, [
    activeConversationId,
    attackResultId,
    getViewedConversationId,
    humanScore,
    isScoreLocked,
    loadConversation,
    onHumanScoreChange,
  ])

  const changeObjective = useCallback(async (newObjective: string, expectedObjective: string): Promise<void> => {
    if (!attackResultId) {
      setPendingObjective(newObjective)
      return
    }

    const updatedAttack = await attacksApi.updateAttack(attackResultId, { objective: newObjective, expected_objective: expectedObjective })
    onAttackChange?.(updatedAttack)
    onObjectiveChange?.(updatedAttack.objective)
  }, [attackResultId, onAttackChange, onObjectiveChange])

  return {
    pendingObjective,
    currentObjective,
    resetPendingObjective,
    updateHumanScore,
    removeHumanScore,
    changeObjective,
  }
}
