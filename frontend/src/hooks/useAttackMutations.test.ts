import { act, renderHook } from '@testing-library/react'

import { attacksApi, scoresApi } from '@/services/api'
import { deferred } from '@/test-utils/deferred'
import type { AttackSummary, BackendScore } from '@/types'

import { useAttackMutations } from './useAttackMutations'
import type { UseAttackMutationsOptions } from './useAttackMutations'

jest.mock('@/services/api', () => ({
  attacksApi: { removeHumanScore: jest.fn(), updateAttack: jest.fn() },
  scoresApi: { createManualScore: jest.fn() },
}))

const attack: AttackSummary = {
  attack_result_id: 'attack', conversation_id: 'main', attack_type: 'ManualAttack', objective: 'Updated objective',
  converters: [], message_count: 2, related_conversation_ids: [], labels: {}, created_at: '', updated_at: '',
}
const score = { id: 'score' } as unknown as BackendScore

function renderMutations(overrides: Partial<UseAttackMutationsOptions> = {}) {
  const options: UseAttackMutationsOptions = {
    attackResultId: 'attack',
    activeConversationId: 'main',
    objective: 'Objective',
    lastResponseMessagePieceId: 'piece',
    humanScore: score,
    isScoreLocked: false,
    loadConversation: jest.fn().mockResolvedValue(undefined),
    getViewedConversationId: () => 'main',
    onHumanScoreChange: jest.fn(),
    onAttackChange: jest.fn(),
    onObjectiveChange: jest.fn(),
    ...overrides,
  }
  return { options, ...renderHook((props: UseAttackMutationsOptions) => useAttackMutations(props), { initialProps: options }) }
}

describe('useAttackMutations', () => {
  beforeEach(() => jest.resetAllMocks())

  describe('updateHumanScore', () => {
    it('should send the exact manual score and reload the viewed conversation', async () => {
      jest.mocked(scoresApi.createManualScore).mockResolvedValue(score)
      const { result, options } = renderMutations()

      await act(async () => { await result.current.updateHumanScore(false, 'Refused') })

      expect(scoresApi.createManualScore).toHaveBeenCalledWith({
        attack_result_id: 'attack', message_id: 'piece', value: false, rationale: 'Refused', update_attack: true,
      })
      expect(options.onHumanScoreChange).toHaveBeenCalledWith(score, 'failure')
      expect(options.loadConversation).toHaveBeenCalledWith('attack', 'main')
    })

    it('should skip the reload when the user navigated away while the score was saving', async () => {
      const save = deferred<BackendScore>()
      jest.mocked(scoresApi.createManualScore).mockReturnValue(save.promise)
      let viewed = 'main'
      const { result, options } = renderMutations({ getViewedConversationId: () => viewed })

      let pending: Promise<void> = Promise.resolve()
      act(() => { pending = result.current.updateHumanScore(true, 'Jailbroken') })
      viewed = 'branch'
      await act(async () => {
        save.resolve(score)
        await pending
      })

      expect(options.onHumanScoreChange).toHaveBeenCalledWith(score, 'success')
      expect(options.loadConversation).not.toHaveBeenCalled()
    })

    it.each([
      ['no attack', { attackResultId: null }],
      ['no response piece', { lastResponseMessagePieceId: null }],
      ['a blank objective', { objective: '   ' }],
      ['a score lock', { isScoreLocked: true }],
    ])('should not save a score with %s', async (_case: string, overrides: Partial<UseAttackMutationsOptions>) => {
      const { result, options } = renderMutations(overrides)

      await act(async () => { await result.current.updateHumanScore(true, 'Rationale') })

      expect(scoresApi.createManualScore).not.toHaveBeenCalled()
      expect(options.onHumanScoreChange).not.toHaveBeenCalled()
    })

    it('should honor a score lock applied after the first render', async () => {
      const { result, rerender, options } = renderMutations()
      rerender({ ...options, isScoreLocked: true })

      await act(async () => { await result.current.updateHumanScore(true, 'Rationale') })

      expect(scoresApi.createManualScore).not.toHaveBeenCalled()
    })

    it('should propagate a failed save without reporting or reloading', async () => {
      jest.mocked(scoresApi.createManualScore).mockRejectedValue(new Error('Save failed'))
      const { result, options } = renderMutations()

      await act(async () => {
        await expect(result.current.updateHumanScore(true, 'Rationale')).rejects.toThrow('Save failed')
      })

      expect(options.onHumanScoreChange).not.toHaveBeenCalled()
      expect(options.loadConversation).not.toHaveBeenCalled()
    })
  })

  describe('removeHumanScore', () => {
    it('should remove the score, fall back to an undetermined outcome and reload', async () => {
      jest.mocked(attacksApi.removeHumanScore).mockResolvedValue({ ...attack, outcome: undefined })
      const { result, options } = renderMutations()

      await act(async () => { await result.current.removeHumanScore() })

      expect(attacksApi.removeHumanScore).toHaveBeenCalledWith('attack')
      expect(options.onHumanScoreChange).toHaveBeenCalledWith(null, 'undetermined')
      expect(options.loadConversation).toHaveBeenCalledWith('attack', 'main')
    })

    it('should report the outcome the backend recalculated', async () => {
      jest.mocked(attacksApi.removeHumanScore).mockResolvedValue({ ...attack, outcome: 'success' })
      const { result, options } = renderMutations({ getViewedConversationId: () => 'branch' })

      await act(async () => { await result.current.removeHumanScore() })

      expect(options.onHumanScoreChange).toHaveBeenCalledWith(null, 'success')
      expect(options.loadConversation).not.toHaveBeenCalled()
    })

    it.each([
      ['no attack', { attackResultId: null }],
      ['no human score', { humanScore: null }],
      ['a score lock', { isScoreLocked: true }],
    ])('should not remove a score with %s', async (_case: string, overrides: Partial<UseAttackMutationsOptions>) => {
      const { result } = renderMutations(overrides)

      await act(async () => { await result.current.removeHumanScore() })

      expect(attacksApi.removeHumanScore).not.toHaveBeenCalled()
    })

    it('should propagate a failed removal without reporting', async () => {
      jest.mocked(attacksApi.removeHumanScore).mockRejectedValue(new Error('Remove failed'))
      const { result, options } = renderMutations()

      await act(async () => { await expect(result.current.removeHumanScore()).rejects.toThrow('Remove failed') })

      expect(options.onHumanScoreChange).not.toHaveBeenCalled()
    })
  })

  describe('changeObjective', () => {
    it('should send the exact objective update and report the attack before the objective', async () => {
      jest.mocked(attacksApi.updateAttack).mockResolvedValue(attack)
      const calls: string[] = []
      const { result } = renderMutations({
        onAttackChange: () => { calls.push('attack') },
        onObjectiveChange: (objective: string) => { calls.push(`objective:${objective}`) },
      })

      await act(async () => { await result.current.changeObjective('New objective', 'Objective') })

      expect(attacksApi.updateAttack).toHaveBeenCalledWith('attack', {
        objective: 'New objective', expected_objective: 'Objective',
      })
      expect(calls).toEqual(['attack', 'objective:Updated objective'])
    })

    it('should keep a pending objective until the attack exists and clear it on reset', async () => {
      const { result, rerender, options } = renderMutations({ attackResultId: null })

      await act(async () => { await result.current.changeObjective('Pending objective', '') })
      expect(result.current.pendingObjective).toBe('Pending objective')
      expect(result.current.currentObjective).toBe('Pending objective')
      expect(attacksApi.updateAttack).not.toHaveBeenCalled()

      rerender({ ...options, attackResultId: 'attack' })
      expect(result.current.currentObjective).toBe('Objective')

      act(() => { result.current.resetPendingObjective() })
      expect(result.current.pendingObjective).toBe('')
    })

    it('should propagate a conflicting objective update without reporting', async () => {
      jest.mocked(attacksApi.updateAttack).mockRejectedValue(new Error('Conflict'))
      const { result, options } = renderMutations()

      await act(async () => {
        await expect(result.current.changeObjective('New objective', 'Stale')).rejects.toThrow('Conflict')
      })

      expect(options.onAttackChange).not.toHaveBeenCalled()
      expect(options.onObjectiveChange).not.toHaveBeenCalled()
    })
  })
})
