import { useCallback, useLayoutEffect, useRef, useState } from 'react'

import { attacksApi, scoresApi } from '@/services/api'
import type { AttackOutcome, AttackSummary, BackendScore, RegisteredComponentReference } from '@/types'

export interface UseAttackMutationsOptions {
  attackResultId: string | null
  activeConversationId: string | null
  /** The loaded attack's objective. */
  objective: string
  defaultChatScorer?: RegisteredComponentReference | null
  lastResponseMessagePieceId?: string | null
  humanScore?: BackendScore | null
  isScoreLocked: boolean
  refreshConversationScores: (attackResultId: string, conversationId: string) => Promise<void>
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
  defaultChatScorer,
  lastResponseMessagePieceId,
  humanScore,
  isScoreLocked,
  refreshConversationScores,
  getViewedConversationId,
  onHumanScoreChange,
  onAttackChange,
  onObjectiveChange,
}: UseAttackMutationsOptions): AttackMutations {
  const [pendingObjective, setPendingObjective] = useState('')
  const currentObjective = attackResultId ? objective : pendingObjective
  const ownerRevisionRef = useRef(0)
  const scoreMutationRevisionRef = useRef(0)
  useLayoutEffect(() => {
    ownerRevisionRef.current += 1
    return () => { ownerRevisionRef.current += 1 }
  }, [attackResultId, activeConversationId])

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

    const ownerRevision = ownerRevisionRef.current
    const mutationRevision = ++scoreMutationRevisionRef.current
    const isCurrentMutation = (): boolean => ownerRevisionRef.current === ownerRevision
      && scoreMutationRevisionRef.current === mutationRevision
    try {
      const score = await scoresApi.createManualScore({
        attack_result_id: attackResultId,
        message_id: lastResponseMessagePieceId,
        value,
        rationale,
        update_attack: true,
      })
      if (!isCurrentMutation()) return
      if (activeConversationId && getViewedConversationId() === activeConversationId) {
        await refreshConversationScores(attackResultId, activeConversationId)
      }
      if (isCurrentMutation()) onHumanScoreChange?.(score, value ? 'success' : 'failure')
    } catch (cause: unknown) {
      if (isCurrentMutation()) throw cause
    }
  }, [
    activeConversationId,
    attackResultId,
    getViewedConversationId,
    isScoreLocked,
    lastResponseMessagePieceId,
    refreshConversationScores,
    onHumanScoreChange,
    currentObjective,
  ])

  const removeHumanScore = useCallback(async (): Promise<void> => {
    if (!attackResultId || !humanScore || isScoreLocked) return

    const ownerRevision = ownerRevisionRef.current
    const mutationRevision = ++scoreMutationRevisionRef.current
    const isCurrentMutation = (): boolean => ownerRevisionRef.current === ownerRevision
      && scoreMutationRevisionRef.current === mutationRevision
    try {
      const attack = await attacksApi.removeHumanScore(attackResultId)
      if (!isCurrentMutation()) return
      if (activeConversationId && getViewedConversationId() === activeConversationId) {
        await refreshConversationScores(attackResultId, activeConversationId)
      }
      if (isCurrentMutation()) onHumanScoreChange?.(null, attack.outcome ?? 'undetermined')
    } catch (cause: unknown) {
      if (isCurrentMutation()) throw cause
    }
  }, [
    activeConversationId,
    attackResultId,
    getViewedConversationId,
    humanScore,
    isScoreLocked,
    refreshConversationScores,
    onHumanScoreChange,
  ])

  const changeObjective = useCallback(async (newObjective: string, expectedObjective: string): Promise<void> => {
    if (!attackResultId) {
      setPendingObjective(newObjective)
      return
    }

    const updatedAttack = await attacksApi.updateAttack(attackResultId, {
      objective: newObjective, expected_objective: expectedObjective,
      ...(!expectedObjective.trim() && newObjective.trim() && defaultChatScorer
        ? { default_chat_scorer: defaultChatScorer } : {}),
    })
    onAttackChange?.(updatedAttack)
    onObjectiveChange?.(updatedAttack.objective)
  }, [attackResultId, onAttackChange, onObjectiveChange, defaultChatScorer])

  return {
    pendingObjective,
    currentObjective,
    resetPendingObjective,
    updateHumanScore,
    removeHumanScore,
    changeObjective,
  }
}
