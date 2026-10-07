import { act, renderHook } from '@testing-library/react'

import { attacksApi, targetsApi } from '@/services/api'
import { makeTarget } from '@/test-utils/targetFixtures'
import type { AddMessageResponse, ConversationSaveInput, NewAttackContext, TargetInstance } from '@/types'

import { useConversationDraft } from './useConversationDraft'

jest.mock('@/services/api', () => ({
  attacksApi: { saveConversation: jest.fn() }, targetsApi: { buildTarget: jest.fn() },
}))

const initial: ConversationSaveInput = {
  messages: [{ id: 'message', role: 'user', pieces: [{ draftId: 'piece', data_type: 'text', original_value: 'Prompt' }] }],
  objective: 'Objective', initialObjective: 'Objective',
  target: null, sourceAttackId: 'attack', sourceConversationId: 'conversation',
}

describe('useConversationDraft', () => {
  beforeEach(() => jest.resetAllMocks())

  const temperatureTarget: TargetInstance = {
    ...makeTarget({
      target_registry_name: 'source', target_type: 'OpenAIChatTarget', temperature: 0.2,
      capabilities: { supports_multi_turn: true, supports_editable_history: true, supported_input_modalities: ['text'] },
    }),
    supports_temperature_override: true,
  }

  it('saves a new attack with a private temperature and returns its target to chat', async () => {
    jest.mocked(targetsApi.buildTarget).mockResolvedValue({
      identifier: { ...temperatureTarget.identifier, hash: 'effective-hash', temperature: 0.8 },
      capabilities: temperatureTarget.capabilities,
    })
    jest.mocked(attacksApi.saveConversation).mockResolvedValue({
      attack: {
        attack_result_id: 'saved', conversation_id: 'saved', attack_type: 'ManualAttack', objective: 'Objective',
        converters: [], message_count: 1, related_conversation_ids: [], labels: {}, created_at: '', updated_at: '',
      },
      messages: { conversation_id: 'saved', messages: [], target_response_status: null },
    })
    const onSaved = jest.fn()
    const { result } = renderHook(() => useConversationDraft())
    act(() => result.current.begin({ ...initial, target: temperatureTarget }))
    act(() => result.current.changeTemperature('0.8'))
    await act(async () => { await result.current.save('new_attack', onSaved) })
    const binding = {
      version: 1, source_name: 'source', source_hash: temperatureTarget.identifier.hash,
      temperature: 0.8, effective_hash: 'effective-hash',
    }
    expect(jest.mocked(attacksApi.saveConversation).mock.calls[0][0].target_binding).toEqual(binding)
    expect(onSaved.mock.calls[0][1].binding).toEqual(binding)
    expect(temperatureTarget.identifier.temperature).toBe(0.2)
  })

  it('rejects temperature changes in the same attack and retains the draft', async () => {
    const { result } = renderHook(() => useConversationDraft())
    act(() => result.current.begin({ ...initial, target: temperatureTarget }))
    act(() => result.current.changeTemperature('0.8'))
    await act(async () => { await result.current.save('same_attack', jest.fn()) })
    expect(result.current.error).toBe('Choose New attack to change the temperature.')
    expect(targetsApi.buildTarget).not.toHaveBeenCalled()
    expect(attacksApi.saveConversation).not.toHaveBeenCalled()
    expect(result.current.draft?.temperature).toBe('0.8')
  })

  it.each([
    { generation: 'gen-1', ready: false },
    { generation: 'gen-2', ready: true },
  ])('preserves the draft when context becomes %j during temperature construction', async (next: NewAttackContext) => {
    const built = {
      identifier: { ...temperatureTarget.identifier, hash: 'effective-hash', temperature: 0.8 },
      capabilities: temperatureTarget.capabilities,
    }
    let finish: (value: typeof built) => void = () => {}
    jest.mocked(targetsApi.buildTarget).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
    jest.mocked(targetsApi.buildTarget).mockResolvedValue(built)
    const onSaved = jest.fn()
    const { result, rerender } = renderHook(
      (context: NewAttackContext) => useConversationDraft(context),
      { initialProps: { generation: 'gen-1', ready: true, labels: { operation: 'original' } } },
    )
    act(() => {
      result.current.begin({ ...initial, target: temperatureTarget })
      result.current.changeTemperature('0.8')
    })
    let pending: Promise<void>
    await act(async () => { pending = result.current.save('new_attack', onSaved) })
    rerender({ ...next, labels: { operation: 'replacement' } })
    await act(async () => { finish(built); await pending })
    expect(result.current.error).toContain('Runtime or default labels changed')
    expect(result.current.draft?.temperature).toBe('0.8')
    expect(result.current.saving).toBe(false)
    expect(attacksApi.saveConversation).not.toHaveBeenCalled()
    expect(onSaved).not.toHaveBeenCalled()
    rerender({ generation: 'gen-2', ready: true, labels: { operation: 'replacement' } })
    jest.mocked(attacksApi.saveConversation).mockResolvedValue({
      attack: {
        attack_result_id: 'saved', conversation_id: 'saved', attack_type: 'ManualAttack', objective: 'Objective',
        converters: [], message_count: 1, related_conversation_ids: [], labels: {}, created_at: '', updated_at: '',
      },
      messages: { conversation_id: 'saved', messages: [], target_response_status: null },
    })
    await act(async () => { await result.current.save('new_attack', onSaved) })
    expect(attacksApi.saveConversation).toHaveBeenCalledWith(expect.objectContaining({
      labels: { operation: 'replacement' }, target_binding: expect.objectContaining({ temperature: 0.8 }),
    }))
    expect(onSaved).toHaveBeenCalledTimes(1)
  })

  it('does not build a temperature target while defaults are loading', async () => {
    const { result } = renderHook(() => useConversationDraft({ generation: 'gen-1', ready: false }))
    act(() => {
      result.current.begin({ ...initial, target: temperatureTarget })
      result.current.changeTemperature('0.8')
    })
    await act(async () => { await result.current.save('new_attack', jest.fn()) })
    expect(result.current.error).toContain('Default labels are not ready')
    expect(targetsApi.buildTarget).not.toHaveBeenCalled()
    expect(attacksApi.saveConversation).not.toHaveBeenCalled()
  })

  it.each(['same_attack', 'new_attack'] as const)(
    'blocks %s for unsupported media and permits a targetless new attack',
    async (destination) => {
      const target = makeTarget({
        target_registry_name: 'text',
        capabilities: {
          supports_multi_turn: true, supports_editable_history: true,
          supported_input_modalities: ['text'],
        },
      })
      const { result } = renderHook(() => useConversationDraft())
      act(() => { result.current.begin({
        ...initial, target, messages: [{
          id: 'media', role: 'user', pieces: [{
            draftId: 'audio', data_type: 'audio_path', original_value: 'history.wav',
          }],
        }],
      }) })
      expect(result.current.targetError).toContain('audio_path')
      const onSaved = jest.fn()
      await act(async () => { await result.current.save(destination, onSaved) })
      expect(attacksApi.saveConversation).not.toHaveBeenCalled()
      expect(result.current.error).toContain('audio_path')
      act(() => { result.current.changeTarget(null) })
      expect(result.current.targetError).toBeUndefined()
      jest.mocked(attacksApi.saveConversation).mockResolvedValue({
        attack: {
          attack_result_id: 'saved', conversation_id: 'saved', attack_type: 'ManualAttack', objective: 'Objective',
          converters: [], message_count: 1, related_conversation_ids: [], labels: {}, created_at: '', updated_at: '',
        },
        messages: { conversation_id: 'saved', messages: [], target_response_status: null },
      })
      await act(async () => { await result.current.save('new_attack', onSaved) })
      expect(onSaved).toHaveBeenCalledTimes(1)
      const request = jest.mocked(attacksApi.saveConversation).mock.calls[0][0]
      expect(request.target_registry_name).toBeUndefined()
      expect(request.messages[0].pieces[0].data_type).toBe('audio_path')
    },
  )

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
