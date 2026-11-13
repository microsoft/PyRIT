import { scenariosApi } from './api'
import { readRecordedScanConversations } from './recordedScanConversations'

jest.mock('./api', () => ({ scenariosApi: { getRunProgress: jest.fn(), getRunConversation: jest.fn() } }))

const record = {
  attack_result_id: 'interrupted', conversation_id: 'recorded', atomic_attack_name: 'direct',
  objective: 'Stored objective', started_at: '2026-01-01T00:00:00Z', seed_identifiers: [],
}
const progress = {
  run: { scenario_result_id: 'scan', scenario_name: 'Scenario', scenario_version: 1,
    status: 'CANCELLED' as const, created_at: '2026-01-01T00:00:00Z' },
  plan: null, results: [], has_more: false, plan_complete: true, recorded_conversations: [record],
  summary: { overall: { completed: 0, planned: 1, succeeded: 0, success_percentage: null, errors: 0, retries: 0 },
    techniques: [], seed_groups: [], atomic_groups: [] },
}

describe('readRecordedScanConversations', () => {
  beforeEach(() => {
    jest.resetAllMocks()
    jest.mocked(scenariosApi.getRunProgress).mockResolvedValue(progress)
    jest.mocked(scenariosApi.getRunConversation).mockResolvedValue({ conversation_id: 'recorded', messages: [], target_response_status: null })
  })

  it('should verify SDK run, attack and selected conversation ownership without inventing results', async () => {
    jest.mocked(scenariosApi.getRunProgress).mockResolvedValue({
      ...progress, recorded_conversations: [record, record, { ...record, conversation_id: 'another-branch' }],
    })
    expect(await readRecordedScanConversations('scan', 'interrupted', 'recorded')).toEqual([
      record, { ...record, conversation_id: 'another-branch' },
    ])
    expect(scenariosApi.getRunConversation).toHaveBeenCalledWith('scan', 'recorded')
    expect(progress.results).toEqual([])
    expect(progress.summary.overall.completed).toBe(0)
  })

  it.each([['unrelated-attack', 'recorded'], ['interrupted', 'unrelated-conversation']])(
    'should reject unrelated ownership %s/%s', async (attackId: string, conversationId: string) => {
      expect(await readRecordedScanConversations('scan', attackId, conversationId)).toBeNull()
      expect(scenariosApi.getRunConversation).not.toHaveBeenCalled()
    },
  )

  it('should reject a mismatched run response and propagate scoped read failures rather than showing empty evidence', async () => {
    jest.mocked(scenariosApi.getRunProgress).mockResolvedValueOnce({ ...progress, run: { ...progress.run, scenario_result_id: 'other' } })
    await expect(readRecordedScanConversations('scan', 'interrupted', 'recorded')).rejects.toThrow('different scan')
    const unavailable = new Error('Stored conversation is unavailable')
    jest.mocked(scenariosApi.getRunConversation).mockRejectedValueOnce(unavailable)
    await expect(readRecordedScanConversations('scan', 'interrupted', 'recorded')).rejects.toBe(unavailable)
  })
})
