import { useState } from 'react'

import { act, renderHook, waitFor } from '@testing-library/react'

import { attacksApi, scoresApi } from '@/services/api'
import { deferred } from '@/test-utils/deferred'
import type { AttackSummary, BackendScore, ConversationMessagesResponse } from '@/types'

import { useAttackMutations } from './useAttackMutations'
import type { UseAttackMutationsOptions } from './useAttackMutations'
import { useConversationMessages } from './useConversationMessages'

jest.mock('@/services/api', () => ({
  attacksApi: { removeHumanScore: jest.fn(), updateAttack: jest.fn(), getMessages: jest.fn() },
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
    refreshConversationScores: jest.fn().mockResolvedValue(undefined),
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
    it('should send the exact manual score and refresh scores in the viewed conversation', async () => {
      jest.mocked(scoresApi.createManualScore).mockResolvedValue(score)
      const { result, options } = renderMutations()

      await act(async () => { await result.current.updateHumanScore(false, 'Refused') })

      expect(scoresApi.createManualScore).toHaveBeenCalledWith({
        attack_result_id: 'attack', message_id: 'piece', value: false, rationale: 'Refused', update_attack: true,
      })
      expect(options.onHumanScoreChange).toHaveBeenCalledWith(score, 'failure')
      expect(options.refreshConversationScores).toHaveBeenCalledWith('attack', 'main')
    })

    it('should skip the score refresh when the user navigated away while the score was saving', async () => {
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
      expect(options.refreshConversationScores).not.toHaveBeenCalled()
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

    it('should propagate a failed save without reporting or refreshing', async () => {
      jest.mocked(scoresApi.createManualScore).mockRejectedValue(new Error('Save failed'))
      const { result, options } = renderMutations()

      await act(async () => {
        await expect(result.current.updateHumanScore(true, 'Rationale')).rejects.toThrow('Save failed')
      })

      expect(options.onHumanScoreChange).not.toHaveBeenCalled()
      expect(options.refreshConversationScores).not.toHaveBeenCalled()
    })
  })

  describe('removeHumanScore', () => {
    it('should remove the score, fall back to an undetermined outcome and refresh scores', async () => {
      jest.mocked(attacksApi.removeHumanScore).mockResolvedValue({ ...attack, outcome: undefined })
      const { result, options } = renderMutations()

      await act(async () => { await result.current.removeHumanScore() })

      expect(attacksApi.removeHumanScore).toHaveBeenCalledWith('attack')
      expect(options.onHumanScoreChange).toHaveBeenCalledWith(null, 'undetermined')
      expect(options.refreshConversationScores).toHaveBeenCalledWith('attack', 'main')
    })

    it('should report the outcome the backend recalculated', async () => {
      jest.mocked(attacksApi.removeHumanScore).mockResolvedValue({ ...attack, outcome: 'success' })
      const { result, options } = renderMutations({ getViewedConversationId: () => 'branch' })

      await act(async () => { await result.current.removeHumanScore() })

      expect(options.onHumanScoreChange).toHaveBeenCalledWith(null, 'success')
      expect(options.refreshConversationScores).not.toHaveBeenCalled()
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

  describe('assessment refresh ownership', () => {
    it.each(['add', 'remove'])('should reject a failed %s score refresh without reporting success', async (action: string) => {
      jest.mocked(scoresApi.createManualScore).mockResolvedValue(score)
      jest.mocked(attacksApi.removeHumanScore).mockResolvedValue(attack)
      const { result, options } = renderMutations({
        refreshConversationScores: jest.fn().mockRejectedValue(new Error('Scores unavailable')),
      })

      await act(async () => {
        await expect(action === 'add'
          ? result.current.updateHumanScore(true, 'Assessment')
          : result.current.removeHumanScore()).rejects.toThrow('Scores unavailable')
      })

      expect(options.onHumanScoreChange).not.toHaveBeenCalled()
      expect(options.refreshConversationScores).toHaveBeenCalledWith('attack', 'main')
    })

    it.each(['add', 'remove'])('should report the %s result only after its score refresh succeeds', async (action: string) => {
      const refresh = deferred<void>()
      jest.mocked(scoresApi.createManualScore).mockResolvedValue(score)
      jest.mocked(attacksApi.removeHumanScore).mockResolvedValue({ ...attack, outcome: 'failure' })
      const { result, options } = renderMutations({ refreshConversationScores: jest.fn(() => refresh.promise) })
      let pending: Promise<void> = Promise.resolve()
      await act(async () => {
        pending = action === 'add' ? result.current.updateHumanScore(true, 'Assessment') : result.current.removeHumanScore()
      })

      expect(options.onHumanScoreChange).not.toHaveBeenCalled()
      expect(options.refreshConversationScores).toHaveBeenCalledWith('attack', 'main')
      await act(async () => { refresh.resolve(); await pending })
      expect(options.onHumanScoreChange).toHaveBeenCalledWith(
        action === 'add' ? score : null, action === 'add' ? 'success' : 'failure',
      )
    })

    it.each([
      ['add', 'attack'], ['add', 'conversation'], ['add', 'unmount'],
      ['remove', 'attack'], ['remove', 'conversation'], ['remove', 'unmount'],
    ])('should ignore a late %s result after a %s owner change', async (action: string, change: string) => {
      const save = deferred<BackendScore>()
      const removal = deferred<AttackSummary>()
      jest.mocked(scoresApi.createManualScore).mockReturnValue(save.promise)
      jest.mocked(attacksApi.removeHumanScore).mockReturnValue(removal.promise)
      const { result, options, rerender, unmount } = renderMutations()
      let pending: Promise<void> = Promise.resolve()
      act(() => {
        pending = action === 'add' ? result.current.updateHumanScore(true, 'Assessment') : result.current.removeHumanScore()
      })
      if (change === 'unmount') unmount()
      else rerender({ ...options, ...(change === 'attack' ? { attackResultId: 'other' } : { activeConversationId: 'branch' }) })
      await act(async () => {
        if (action === 'add') save.resolve(score)
        else removal.resolve(attack)
        await pending
      })

      expect(options.onHumanScoreChange).not.toHaveBeenCalled()
      expect(options.refreshConversationScores).not.toHaveBeenCalled()
    })

    it.each(['add', 'remove'])('should not report a stale %s after its refresh finishes in a different attack', async (action: string) => {
      const refresh = deferred<void>()
      jest.mocked(scoresApi.createManualScore).mockResolvedValue(score)
      jest.mocked(attacksApi.removeHumanScore).mockResolvedValue(attack)
      const { result, options, rerender } = renderMutations({ refreshConversationScores: jest.fn(() => refresh.promise) })
      let pending: Promise<void> = Promise.resolve()
      await act(async () => {
        pending = action === 'add' ? result.current.updateHumanScore(true, 'Assessment') : result.current.removeHumanScore()
      })
      rerender({ ...options, attackResultId: 'other' })
      await act(async () => { refresh.resolve(); await pending })

      expect(options.onHumanScoreChange).not.toHaveBeenCalled()
    })

    it('should let a newer removal supersede an earlier pending addition', async () => {
      const save = deferred<BackendScore>()
      jest.mocked(scoresApi.createManualScore).mockReturnValueOnce(save.promise)
      jest.mocked(attacksApi.removeHumanScore).mockResolvedValue({ ...attack, outcome: 'failure' })
      const { result, options } = renderMutations()
      let pending: Promise<void> = Promise.resolve()
      act(() => { pending = result.current.updateHumanScore(true, 'Assessment') })
      await act(async () => { await result.current.removeHumanScore() })
      await act(async () => { save.resolve(score); await pending })

      expect(options.refreshConversationScores).toHaveBeenCalledTimes(1)
      expect(options.onHumanScoreChange).toHaveBeenCalledTimes(1)
      expect(options.onHumanScoreChange).toHaveBeenCalledWith(null, 'failure')
    })

    it('should ignore a superseded refresh failure rather than overwrite the newest assessment', async () => {
      const olderRefresh = deferred<void>()
      const newerScore = { ...score, id: 'newer' }
      const refreshConversationScores = jest.fn().mockReturnValueOnce(olderRefresh.promise).mockResolvedValueOnce(undefined)
      jest.mocked(scoresApi.createManualScore).mockResolvedValueOnce(score).mockResolvedValueOnce(newerScore)
      const { result, options } = renderMutations({ refreshConversationScores })
      let pending: Promise<void> = Promise.resolve()
      await act(async () => { pending = result.current.updateHumanScore(true, 'Older assessment') })
      await act(async () => { await result.current.updateHumanScore(false, 'Newer assessment') })
      await act(async () => { olderRefresh.reject(new Error('Stale failure')); await pending })

      expect(options.onHumanScoreChange).toHaveBeenCalledTimes(1)
      expect(options.onHumanScoreChange).toHaveBeenCalledWith(newerScore, 'failure')
    })
  })

  it('should preserve all 150 saved pieces through add/remove callbacks and metadata rerenders using only bounded reads', async () => {
    const pieceIds = Array.from({ length: 150 }, (_value: unknown, index: number) => `piece-${String(index).padStart(3, '0')}`)
    const assessment: BackendScore = {
      id: 'assessment', message_piece_id: pieceIds[149], scorer_type: 'ManualScorer',
      score_type: 'true_false', score_value: 'True', timestamp: '2026-01-01T00:01:00Z',
    }
    function savedPage(start: number, end: number, nextCursor: string | null, hasAssessment: boolean = false): ConversationMessagesResponse {
      return {
        conversation_id: 'main',
        messages: [{
          turn_number: 1, role: 'assistant', created_at: '2026-01-01T00:00:00Z',
          message_pieces: pieceIds.slice(start, end).map((id: string) => ({
            id, original_value: id, converted_value: id, original_value_data_type: 'text',
            converted_value_data_type: 'text', response_error: 'none',
            scores: hasAssessment && id === assessment.message_piece_id ? [assessment] : [],
          })),
        }],
        target_response_status: null,
        pagination: { limit: 100, has_more: nextCursor !== null, next_cursor: nextCursor },
        partial_message_sequences: [1],
      }
    }
    jest.mocked(attacksApi.getMessages).mockResolvedValueOnce(savedPage(0, 100, 'initial'))
      .mockResolvedValueOnce(savedPage(100, 150, null))
      .mockResolvedValueOnce(savedPage(0, 100, 'added'))
      .mockResolvedValueOnce(savedPage(100, 150, null, true))
      .mockResolvedValueOnce(savedPage(0, 100, 'removed'))
      .mockResolvedValueOnce(savedPage(100, 150, null))
    jest.mocked(scoresApi.createManualScore).mockResolvedValue(assessment)
    jest.mocked(attacksApi.removeHumanScore).mockResolvedValue({ ...attack, outcome: 'undetermined', human_score: null })
    const { result } = renderHook(() => {
      const [humanScore, setHumanScore] = useState<BackendScore | null>(null)
      const conversation = useConversationMessages({
        attackResultId: 'attack', conversationId: 'main', activeConversationId: 'main',
        messagePageSize: 100, sendingConversations: new Set<string>(), isSendInFlight: () => false,
      })
      const mutations = useAttackMutations({
        attackResultId: 'attack', activeConversationId: 'main', objective: 'Objective',
        lastResponseMessagePieceId: pieceIds[149], humanScore, isScoreLocked: conversation.isTranscriptIncomplete,
        refreshConversationScores: conversation.refreshConversationScores,
        getViewedConversationId: conversation.getViewedConversationId, onHumanScoreChange: setHumanScore,
      })
      return { conversation, mutations, humanScore }
    })
    await waitFor(() => expect(result.current.conversation.loadedPieceCount).toBe(100))
    await act(async () => { await result.current.mutations.updateHumanScore(true, 'Incomplete history') })
    expect(scoresApi.createManualScore).not.toHaveBeenCalled()
    await act(async () => { await result.current.conversation.loadMoreMessages() })
    const content = result.current.conversation.messages[0].content
    const pagination = result.current.conversation.messagePagination

    await act(async () => { await result.current.mutations.updateHumanScore(true, 'Human assessment') })
    expect(result.current.humanScore).toBe(assessment)
    expect(result.current.conversation.messages[0].displayPieces?.at(-1)?.scores).toEqual([
      expect.objectContaining(assessment),
    ])
    expect(result.current.conversation.messages[0].content).toBe(content)
    expect(result.current.conversation.loadedPieceCount).toBe(150)
    expect(result.current.conversation.messagePagination).toBe(pagination)
    expect(result.current.conversation.isTranscriptIncomplete).toBe(false)

    await act(async () => { await result.current.mutations.removeHumanScore() })
    expect(result.current.humanScore).toBeNull()
    expect(result.current.conversation.messages[0].displayPieces?.at(-1)?.scores).toBeUndefined()
    expect(result.current.conversation.messages[0].content).toBe(content)
    expect(result.current.conversation.loadedPieceCount).toBe(150)
    expect(result.current.conversation.messagePagination).toBe(pagination)
    expect(result.current.conversation.messages[0].isPartial).toBe(false)
    expect(result.current.conversation.isTranscriptIncomplete).toBe(false)
    expect(attacksApi.getMessages).toHaveBeenCalledTimes(6)
    expect(jest.mocked(attacksApi.getMessages).mock.calls.map((call: Parameters<typeof attacksApi.getMessages>) => call[2])).toEqual([
      { limit: 100 }, { limit: 100, cursor: 'initial' },
      { limit: 100 }, { limit: 100, cursor: 'added' },
      { limit: 100 }, { limit: 100, cursor: 'removed' },
    ])
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
