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
