import type { BackendScore } from '@/types'

import { scoreSummaryFromBackendScore, scoreSummaryValue } from './scoreSummary'

describe('scoreSummary', () => {
  it('maps a message-piece score without losing its role', () => {
    const score: BackendScore = {
      id: 'score-1', message_piece_id: 'piece-1', scorer_type: 'Judge', score_type: 'true_false',
      score_value: 'false', status: 'complete', is_objective_score: false, score_category: ['harm'],
      score_rationale: 'Refused.', timestamp: '2026-01-01T00:00:00Z',
    }
    expect(scoreSummaryFromBackendScore(score)).toEqual({
      scorer: 'Judge', scoreType: 'true_false', value: 'false', status: 'complete',
      rationale: 'Refused.', categories: ['harm'], isObjective: false,
    })
  })

  it('names the status of a score without a value', () => {
    expect(scoreSummaryValue({ scorer: 'Judge', scoreType: 'true_false', value: 'true', status: 'complete' })).toBe('true')
    expect(scoreSummaryValue({ scorer: 'Judge', scoreType: 'true_false', value: null, status: 'undetermined' })).toBe('undetermined')
    expect(scoreSummaryValue({ scorer: 'Judge', scoreType: 'true_false' })).toBe('')
  })
})
