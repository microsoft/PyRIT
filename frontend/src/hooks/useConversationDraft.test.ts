import { act, renderHook } from '@testing-library/react'

import { attacksApi } from '@/services/api'
import { makeTarget } from '@/test-utils/targetFixtures'
import type { AddMessageResponse, ConversationSaveInput } from '@/types'

import { useConversationDraft } from './useConversationDraft'

jest.mock('@/services/api', () => ({ attacksApi: { saveConversation: jest.fn() } }))

const initial: ConversationSaveInput = {
  messages: [{ id: 'message', role: 'user', pieces: [{ draftId: 'piece', data_type: 'text', original_value: 'Prompt' }] }],
  objective: 'Objective', initialObjective: 'Objective',
  target: null, sourceAttackId: 'attack', sourceConversationId: 'conversation',
}

describe('useConversationDraft', () => {
  beforeEach(() => jest.resetAllMocks())

  it('protects target-only changes and ignores target object replacement with the same identity', () => {
    const target = makeTarget({ target_registry_name: 'first' })
    const { result } = renderHook(() => useConversationDraft())
    act(() => { result.current.begin({ ...initial, target }) })
    expect(result.current.shouldBlock()).toBe(false)
    act(() => { result.current.changeTarget({ ...target, identifier: { ...target.identifier } }) })
    expect(result.current.dirty).toBe(false)
    act(() => { result.current.changeTarget(null) })
    expect(result.current.dirty).toBe(true)
    expect(result.current.shouldBlock()).toBe(true)
    act(() => { result.current.changeTarget(target) })
    expect(result.current.shouldBlock()).toBe(false)
    act(() => { result.current.changeTarget({ ...target, identifier: { ...target.identifier, hash: 'changed' } }) })
    expect(result.current.shouldBlock()).toBe(true)
  })

  it('retains a failed draft and its error', async () => {
    jest.mocked(attacksApi.saveConversation).mockRejectedValue(new Error('Save failed'))
    const onSaved = jest.fn()
    const { result } = renderHook(() => useConversationDraft())
    act(() => { result.current.begin(initial); result.current.changeObjective('Changed') })
    await act(async () => { await result.current.save('new_attack', onSaved) })
    expect(result.current.draft?.objective).toBe('Changed')
    expect(result.current.error).toBe('Save failed')
    expect(result.current.shouldBlock()).toBe(true)
    expect(onSaved).not.toHaveBeenCalled()
  })

  it('does not complete an old save into a replacement draft', async () => {
    let finish: (response: AddMessageResponse) => void = () => {}
    jest.mocked(attacksApi.saveConversation).mockImplementation(() => new Promise((resolve) => { finish = resolve }))
    const onSaved = jest.fn()
    const { result } = renderHook(() => useConversationDraft())
    act(() => { result.current.begin(initial) })
    let pending: Promise<void>
    await act(async () => { pending = result.current.save('new_attack', onSaved) })
    act(() => { result.current.begin({ ...initial, objective: 'Replacement', sourceConversationId: 'other' }) })
    await act(async () => {
      finish({
        attack: {
          attack_result_id: 'saved', conversation_id: 'saved', attack_type: 'ManualAttack', objective: 'Objective',
          converters: [], message_count: 1, related_conversation_ids: [], labels: {}, created_at: '', updated_at: '',
        },
        messages: { conversation_id: 'saved', messages: [], target_response_status: null },
      })
      await pending
    })
    expect(onSaved).not.toHaveBeenCalled()
    expect(result.current.draft?.objective).toBe('Replacement')
    expect(result.current.draft?.sourceConversationId).toBe('other')
  })

  it('owns uploaded URLs until the draft is discarded or replaced', () => {
    const { result } = renderHook(() => useConversationDraft())
    act(() => { result.current.begin(initial) })
    act(() => { result.current.changeAttachments('piece', [{
      draftId: 'upload', type: 'file', url: 'blob:owned', name: 'file.txt', mimeType: 'text/plain',
      file: new File(['bytes'], 'file.txt'),
    }]) })
    expect(result.current.draft?.messages[0].pieces).toHaveLength(2)
    act(() => { result.current.discard() })
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:owned')
    expect(result.current.draft).toBeNull()
    expect(result.current.shouldBlock()).toBe(false)
  })
})
