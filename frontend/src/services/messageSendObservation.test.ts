import { attacksApi } from '@/services/api'
import { deferred } from '@/test-utils/deferred'
import type { AttackSummary, MessageSendStatus } from '@/types'

import { observeMessageSend } from './messageSendObservation'

jest.mock('@/services/api', () => ({
  attacksApi: { getMessageSend: jest.fn(), getAttack: jest.fn() },
}))

const completed: MessageSendStatus = {
  attack_result_id: 'attack', conversation_id: 'conversation', send_id: 'send',
  state: 'completed', request_turn_number: 0, failure_stage: null, error: null,
}
const attack: AttackSummary = {
  attack_result_id: 'attack', conversation_id: 'conversation', attack_type: 'ManualAttack',
  objective: '', converters: [], related_conversation_ids: [], message_count: 2,
  labels: {}, created_at: '', updated_at: '',
}

describe('observeMessageSend', () => {
  beforeEach(() => {
    jest.resetAllMocks()
    jest.mocked(attacksApi.getAttack).mockResolvedValue(attack)
  })

  it('keeps a completed sibling visible while the owner polls the next shared snapshot', async () => {
    const pending = deferred<MessageSendStatus>()
    const nextPoll = deferred<void>()
    const partial: MessageSendStatus = {
      ...completed, state: 'sending',
      conversations: [{ ...completed, conversation_id: 'copy', state: 'completed' }],
    }
    jest.mocked(attacksApi.getMessageSend).mockResolvedValueOnce(partial).mockImplementation(() => {
      nextPoll.resolve()
      return pending.promise
    })
    const onConfirmed = jest.fn()
    const observation = observeMessageSend('attack', Promise.resolve({ ...completed, state: 'queued' }), onConfirmed, jest.fn())
    await nextPoll.promise
    expect(attacksApi.getMessageSend).toHaveBeenCalledTimes(2)
    expect(await observation.readProgress()).toEqual(partial)
    expect(await observation.readAttack()).toEqual(attack)
    expect(onConfirmed).toHaveBeenCalledTimes(1)
    const finalAttack = { ...attack, message_count: 6 }
    jest.mocked(attacksApi.getAttack).mockResolvedValue(finalAttack)
    pending.resolve(completed)
    expect(await observation.readProgress()).toEqual(completed)
    expect(await observation.readAttack()).toEqual(finalAttack)
    expect(onConfirmed).toHaveBeenCalledTimes(1)
    expect(attacksApi.getAttack).toHaveBeenCalledTimes(2)
  })

  it('retains read errors until an explicit refresh, without retrying the submission', async () => {
    const failure = new Error('Read unavailable')
    jest.mocked(attacksApi.getMessageSend).mockRejectedValueOnce(failure).mockResolvedValue(completed)
    const errorReported = deferred<void>()
    const onError = jest.fn(() => { errorReported.resolve() })
    const observation = observeMessageSend('attack', Promise.resolve({ ...completed, state: 'queued' }), jest.fn(), onError)
    await observation.readProgress()
    await errorReported.promise
    await expect(observation.readProgress()).rejects.toThrow(failure)
    await expect(observation.readProgress()).rejects.toThrow(failure)
    expect(attacksApi.getMessageSend).toHaveBeenCalledTimes(1)
    expect(onError).toHaveBeenCalledWith(failure)
    expect(await observation.readProgress(true)).toEqual(completed)
    expect(attacksApi.getMessageSend).toHaveBeenCalledTimes(2)
  })

  it('refreshes an in-flight sibling read before delivering terminal metadata', async () => {
    const oldRead = deferred<AttackSummary>()
    const terminal = deferred<MessageSendStatus>()
    const polling = deferred<void>()
    const finalAttack = { ...attack, message_count: 6, outcome: 'success' as const }
    jest.mocked(attacksApi.getAttack).mockReturnValueOnce(oldRead.promise).mockResolvedValue(finalAttack)
    jest.mocked(attacksApi.getMessageSend).mockResolvedValueOnce({
      ...completed, state: 'sending', conversations: [{ ...completed }],
    }).mockImplementation(() => {
      polling.resolve()
      return terminal.promise
    })
    const observation = observeMessageSend('attack', Promise.resolve({
      ...completed, state: 'queued', request_turn_number: null,
    }), jest.fn(), jest.fn())
    await polling.promise
    await observation.readProgress()
    const siblingRead = observation.readAttack()
    terminal.resolve(completed)
    await observation.readProgress()
    const finalRead = observation.readAttack()
    oldRead.resolve(attack)
    expect(await finalRead).toEqual(finalAttack)
    expect(await siblingRead).toEqual(finalAttack)
    expect(attacksApi.getAttack).toHaveBeenCalledTimes(2)
  })

  it.each(['queued', 'preparing', 'interrupted'] as const)(
    'does not confirm allocated turns without dispatch evidence while %s',
    async (state) => {
      const progress: MessageSendStatus = {
        ...completed, state: 'failed', failure_stage: 'preparation',
        conversations: [
          { ...completed, state: 'failed', failure_stage: 'preparation' },
          { ...completed, state, failure_stage: state === 'interrupted' ? 'interrupted' : null },
        ],
      }
      const onConfirmed = jest.fn()
      const observation = observeMessageSend('attack', Promise.resolve(progress), onConfirmed, jest.fn())
      await observation.readProgress()
      await observation.readAttack()
      expect(onConfirmed).not.toHaveBeenCalled()
    },
  )

  it.each([
    { target_invoked: false, state: 'completed' as const, failure_stage: null, expected: 0 },
    { target_invoked: false, state: 'interrupted' as const, failure_stage: 'interrupted' as const, expected: 0 },
    { target_invoked: true, state: 'interrupted' as const, failure_stage: 'interrupted' as const, expected: 1 },
    { target_invoked: true, state: 'failed' as const, failure_stage: 'sending' as const, expected: 1 },
  ])('uses retained dispatch evidence for $state ($target_invoked)', async (
    { target_invoked, state, failure_stage, expected },
  ) => {
    const onConfirmed = jest.fn()
    const observation = observeMessageSend('attack', Promise.resolve({
      ...completed, target_invoked, state, failure_stage, conversations: [],
    }), onConfirmed, jest.fn())
    await observation.readProgress()
    await observation.readAttack()
    expect(onConfirmed).toHaveBeenCalledTimes(expected)
  })

  it('does not manufacture confirmed use after acceptance fails, even when saved evidence exists', async () => {
    const onConfirmed = jest.fn()
    const onError = jest.fn()
    const observation = observeMessageSend('attack', Promise.reject(new Error('Acceptance unknown')), onConfirmed, onError)
    await expect(observation.readProgress()).rejects.toThrow('Acceptance unknown')
    expect(await observation.readAttack()).toEqual(attack)
    expect(onConfirmed).not.toHaveBeenCalled()
    expect(onError).toHaveBeenCalled()
    expect(attacksApi.getMessageSend).not.toHaveBeenCalled()
  })

  it('shares metadata failures and confirms once after an explicit evidence refresh', async () => {
    const failure = new Error('Metadata unavailable')
    jest.mocked(attacksApi.getAttack).mockRejectedValueOnce(failure).mockResolvedValue(attack)
    const onConfirmed = jest.fn()
    const observation = observeMessageSend('attack', Promise.resolve(completed), onConfirmed, jest.fn())
    await observation.readProgress()
    await expect(observation.readAttack()).rejects.toThrow(failure)
    await expect(observation.readAttack()).rejects.toThrow(failure)
    expect(attacksApi.getAttack).toHaveBeenCalledTimes(1)
    expect(await observation.readAttack(true)).toEqual(attack)
    expect(onConfirmed).toHaveBeenCalledTimes(1)
    expect(await observation.readAttack()).toEqual(attack)
    expect(attacksApi.getAttack).toHaveBeenCalledTimes(2)
  })
})
