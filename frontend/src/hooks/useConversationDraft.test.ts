import { act, renderHook } from '@testing-library/react'

import { attacksApi, targetsApi } from '@/services/api'
import { makeTarget } from '@/test-utils/targetFixtures'
import type { AddMessageResponse, ConversationSaveInput, NewAttackContext, TargetInstance } from '@/types'
import * as conversationDraft from '@/utils/conversationDraft'

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
  afterEach(() => jest.restoreAllMocks())

  const registeredTarget = makeTarget({
    target_registry_name: 'source', target_type: 'OpenAIChatTarget', temperature: 0.2,
    capabilities: { supports_multi_turn: true, supports_editable_history: true, supported_input_modalities: ['text'] },
  })
  const savedResponse: AddMessageResponse = {
    attack: {
      attack_result_id: 'saved', conversation_id: 'saved', attack_type: 'ManualAttack', objective: 'Objective',
      converters: [], message_count: 1, related_conversation_ids: [], labels: {}, created_at: '', updated_at: '',
    },
    messages: { conversation_id: 'saved', messages: [], target_response_status: null },
  }

  it.each(['same_attack', 'new_attack'] as const)(
    'saves %s with the selected target without constructing a variant',
    async (destination) => {
      jest.mocked(attacksApi.saveConversation).mockResolvedValue(savedResponse)
      const onSaved = jest.fn()
      const { result } = renderHook(() => useConversationDraft())
      act(() => result.current.begin({ ...initial, target: registeredTarget }))
      await act(async () => { await result.current.save(destination, onSaved) })
      const request = jest.mocked(attacksApi.saveConversation).mock.calls[0][0]
      expect(request.target_registry_name).toBe('source')
      expect(request).not.toHaveProperty('target_binding')
      expect(targetsApi.buildTarget).not.toHaveBeenCalled()
      expect(onSaved).toHaveBeenCalledWith(savedResponse)
      expect(registeredTarget.identifier.temperature).toBe(0.2)
    },
  )

  it.each(['same_attack', 'new_attack'] as const)(
    'preserves an existing binding and historical generation metadata when saving %s',
    async (destination) => {
      const binding = {
        version: 1 as const, source_name: 'source', source_hash: registeredTarget.identifier.hash,
        temperature: 0.8, effective_hash: 'effective-hash',
      }
      const target: TargetInstance = {
        ...registeredTarget, binding,
        identifier: { ...registeredTarget.identifier, hash: binding.effective_hash, temperature: 0.8 },
      }
      const metadata = {
        request_generation_config: { temperature: 0 },
        _pyrit_generation: {
          temperature: { requested: 0, resolved: 0, source: 'request' },
          request_piece_ids: ['original-piece'],
        },
        custom: 'preserved',
      }
      jest.mocked(attacksApi.saveConversation).mockResolvedValue(savedResponse)
      const { result } = renderHook(() => useConversationDraft())
      act(() => result.current.begin({
        ...initial, target,
        messages: [{ ...initial.messages[0], pieces: [{
          ...initial.messages[0].pieces[0], source_piece_id: 'original-piece', prompt_metadata: metadata,
        }] }],
      }))
      await act(async () => { await result.current.save(destination, jest.fn()) })
      const request = jest.mocked(attacksApi.saveConversation).mock.calls[0][0]
      expect(request.target_binding).toEqual(binding)
      expect(request.messages[0].pieces[0].prompt_metadata).toEqual(metadata)
      expect(target.binding).toEqual(binding)
      expect(target.identifier.temperature).toBe(0.8)
      expect(targetsApi.buildTarget).not.toHaveBeenCalled()
    },
  )

  it.each([
    { generation: 'gen-1', ready: false },
    { generation: 'gen-2', ready: true },
  ])('preserves the draft when context becomes %j during serialization', async (next: NewAttackContext) => {
    let finish: (value: Awaited<ReturnType<typeof conversationDraft.serializeDraft>>) => void = () => {}
    jest.spyOn(conversationDraft, 'serializeDraft').mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
    const onSaved = jest.fn()
    const { result, rerender } = renderHook(
      (context: NewAttackContext) => useConversationDraft(context),
      { initialProps: { generation: 'gen-1', ready: true, labels: { operation: 'original' } } },
    )
    act(() => {
      result.current.begin({ ...initial, target: registeredTarget })
      result.current.changeObjective('Edited objective')
    })
    let pending: Promise<boolean>
    await act(async () => { pending = result.current.save('new_attack', onSaved) })
    rerender({ ...next, labels: { operation: 'replacement' } })
    await act(async () => { finish([{ role: 'user', pieces: [{ data_type: 'text', original_value: 'Prompt' }] }]); await pending })
    expect(result.current.error).toContain('Runtime or default labels changed')
    expect(result.current.draft?.objective).toBe('Edited objective')
    expect(result.current.saving).toBe(false)
    expect(attacksApi.saveConversation).not.toHaveBeenCalled()
    expect(onSaved).not.toHaveBeenCalled()
    rerender({ generation: 'gen-2', ready: true, labels: { operation: 'replacement' } })
    jest.mocked(attacksApi.saveConversation).mockResolvedValue(savedResponse)
    await act(async () => { await result.current.save('new_attack', onSaved) })
    expect(attacksApi.saveConversation).toHaveBeenCalledWith(expect.objectContaining({
      labels: { operation: 'replacement' }, target_registry_name: 'source', objective: 'Edited objective',
    }))
    expect(onSaved).toHaveBeenCalledTimes(1)
  })

  it('does not save while defaults are loading', async () => {
    const { result } = renderHook(() => useConversationDraft({ generation: 'gen-1', ready: false }))
    act(() => {
      result.current.begin({ ...initial, target: registeredTarget })
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
