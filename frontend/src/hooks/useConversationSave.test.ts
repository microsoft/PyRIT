import { act, renderHook } from '@testing-library/react'

import { attacksApi } from '@/services/api'
import type { AddMessageResponse, ConversationSaveInput, NewAttackContext } from '@/types'
import * as conversationDraft from '@/utils/conversationDraft'

import { useConversationSave } from './useConversationSave'

jest.mock('@/services/api', () => ({ attacksApi: { saveConversation: jest.fn() } }))

const input: ConversationSaveInput = {
  messages: [{ id: 'message', role: 'user', pieces: [{ draftId: 'piece', data_type: 'text', original_value: 'Prompt' }] }],
  objective: 'Objective', initialObjective: 'Objective', target: null,
  sourceAttackId: 'source-attack', sourceConversationId: 'source-conversation', labels: { operator: 'owner' },
}
const response: AddMessageResponse = {
  attack: {
    attack_result_id: 'saved-attack', conversation_id: 'saved', attack_type: 'ManualAttack', objective: 'Objective',
    converters: [], message_count: 1, related_conversation_ids: [], labels: {}, created_at: '', updated_at: '',
  },
  messages: { conversation_id: 'saved', messages: [], target_response_status: null },
}

describe('useConversationSave', () => {
  beforeEach(() => jest.resetAllMocks())
  afterEach(() => jest.restoreAllMocks())

  it('uses the personal scorer only for a new attack, not a same-attack save', async () => {
    jest.mocked(attacksApi.saveConversation).mockResolvedValue(response)
    const chatScorer = { registry_name: 'default-judge', identifier_hash: 'full-hash' }
    const { result } = renderHook(() => useConversationSave({ generation: 'ready', ready: true, chatScorer }))
    await act(async () => { await result.current.save(input, 'new_attack') })
    expect(jest.mocked(attacksApi.saveConversation).mock.calls[0][0].chat_scorer).toEqual(chatScorer)
    await act(async () => { await result.current.save(input, 'same_attack') })
    expect(jest.mocked(attacksApi.saveConversation).mock.calls[1][0]).not.toHaveProperty('chat_scorer')
  })

  it('leaves blank new chats unconfigured but persists an explicit Off choice', async () => {
    jest.mocked(attacksApi.saveConversation).mockResolvedValue(response)
    const context: NewAttackContext = {
      generation: 'ready', ready: true, chatScorer: { registry_name: 'judge', identifier_hash: 'hash' },
    }
    const view = renderHook((props: NewAttackContext) => useConversationSave(props), { initialProps: context })
    await act(async () => { await view.result.current.save({ ...input, objective: '' }, 'new_attack') })
    expect(jest.mocked(attacksApi.saveConversation).mock.calls[0][0]).not.toHaveProperty('chat_scorer')
    view.rerender({ ...context, chatScorer: null, chatScorerExplicit: true })
    await act(async () => { await view.result.current.save({ ...input, objective: '' }, 'new_attack') })
    expect(jest.mocked(attacksApi.saveConversation).mock.calls[1][0]).toHaveProperty('chat_scorer', null)
  })

  it('suggests a personal default only when a same-attack save introduces the first objective', async () => {
    jest.mocked(attacksApi.saveConversation).mockResolvedValue(response)
    const chatScorer = { registry_name: 'judge', identifier_hash: 'hash' }
    const { result } = renderHook(() => useConversationSave({ generation: 'ready', ready: true, chatScorer }))
    await act(async () => { await result.current.save({ ...input, initialObjective: '' }, 'same_attack') })
    expect(jest.mocked(attacksApi.saveConversation).mock.calls[0][0]).toMatchObject({
      default_chat_scorer: chatScorer, expected_objective: '',
    })
    expect(jest.mocked(attacksApi.saveConversation).mock.calls[0][0]).not.toHaveProperty('chat_scorer')
  })

  it('reuses the attempt after a lost response, then starts a new intentional copy after success', async () => {
    jest.mocked(attacksApi.saveConversation).mockRejectedValueOnce(new Error('Response lost')).mockResolvedValue(response)
    const { result } = renderHook(() => useConversationSave())
    await act(async () => { await expect(result.current.save(input, 'new_attack')).rejects.toThrow('Response lost') })
    await act(async () => { await result.current.save(input, 'new_attack') })
    await act(async () => { await result.current.save(input, 'new_attack') })
    const requests = jest.mocked(attacksApi.saveConversation).mock.calls.map(([request]) => request)
    expect(requests[0]).toEqual(requests[1])
    expect(requests[2].save_id).not.toBe(requests[1].save_id)
    expect(requests[0]).toMatchObject({
      source_attack_result_id: 'source-attack', source_conversation_id: 'source-conversation', objective: 'Objective',
      messages: [{ role: 'user', pieces: [{ original_value: 'Prompt' }] }],
    })
  })

  it('starts a new attempt when content or destination changes after failure', async () => {
    jest.mocked(attacksApi.saveConversation).mockRejectedValue(new Error('Response lost'))
    const { result } = renderHook(() => useConversationSave())
    await act(async () => { await expect(result.current.save(input, 'new_attack')).rejects.toThrow() })
    await act(async () => { await expect(result.current.save({ ...input, objective: 'Changed' }, 'new_attack')).rejects.toThrow() })
    await act(async () => { await expect(result.current.save({ ...input, objective: 'Changed' }, 'same_attack')).rejects.toThrow() })
    const ids = jest.mocked(attacksApi.saveConversation).mock.calls.map(([request]) => request.save_id)
    expect(new Set(ids).size).toBe(3)
  })

  it('rejects a double submission while the first request is pending', async () => {
    let finish: (value: AddMessageResponse) => void = () => {}
    jest.mocked(attacksApi.saveConversation).mockImplementation(() => new Promise((resolve) => { finish = resolve }))
    const { result } = renderHook(() => useConversationSave())
    let pending: Promise<AddMessageResponse>
    await act(async () => {
      pending = result.current.save(input, 'new_attack')
      await expect(result.current.save(input, 'new_attack')).rejects.toThrow('already in progress')
    })
    expect(result.current.saving).toBe(true)
    expect(attacksApi.saveConversation).toHaveBeenCalledTimes(1)
    await act(async () => { finish(response); await pending })
    expect(result.current.saving).toBe(false)
  })

  it.each(['Objective', 'Changed', ''])('only sends an objective update when changed: %j', async (objective) => {
    jest.mocked(attacksApi.saveConversation).mockResolvedValue(response)
    const { result } = renderHook(() => useConversationSave())
    await act(async () => { await result.current.save({ ...input, objective }, 'same_attack') })
    const request = jest.mocked(attacksApi.saveConversation).mock.calls[0][0]
    const changed = objective !== input.initialObjective
    expect(request.objective).toBe(changed ? objective : undefined)
    expect(request.expected_objective).toBe(changed ? input.initialObjective : undefined)
  })

  it('blocks new attacks while defaults load but still saves within an existing attack', async () => {
    jest.mocked(attacksApi.saveConversation).mockResolvedValue(response)
    const context: NewAttackContext = { generation: 'gen-1', ready: false, labels: { team: 'new' }, operationId: 'new-id' }
    const { result } = renderHook(() => useConversationSave(context))
    await act(async () => {
      await expect(result.current.save(input, 'new_attack')).rejects.toThrow('Default labels are not ready')
    })
    expect(attacksApi.saveConversation).not.toHaveBeenCalled()
    await act(async () => { await result.current.save(input, 'same_attack') })
    expect(attacksApi.saveConversation).toHaveBeenCalledWith(expect.objectContaining({
      destination: 'same_attack', labels: input.labels,
    }))
    expect(jest.mocked(attacksApi.saveConversation).mock.calls[0][0].operation_id).toBeUndefined()
  })

  it.each([
    { generation: 'gen-1', ready: false },
    { generation: 'gen-2', ready: true },
  ])('rejects a new attack after delayed serialization when context becomes %j', async (next: NewAttackContext) => {
    let finish: (messages: Awaited<ReturnType<typeof conversationDraft.serializeDraft>>) => void = () => {}
    jest.spyOn(conversationDraft, 'serializeDraft').mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
    jest.mocked(attacksApi.saveConversation).mockResolvedValue(response)
    const context: NewAttackContext = { generation: 'gen-1', ready: true, labels: { team: 'old' }, operationId: 'old-id' }
    const { result, rerender } = renderHook(
      (props: NewAttackContext) => useConversationSave(props), { initialProps: context },
    )
    let pending: Promise<AddMessageResponse>
    await act(async () => { pending = result.current.save(input, 'new_attack') })
    rerender({ ...next, labels: { team: 'new' }, operationId: 'new-id' })
    await act(async () => {
      finish([{ role: 'user', pieces: [{ data_type: 'text', original_value: 'Prompt' }] }])
      await expect(pending).rejects.toThrow('Runtime or default labels changed')
    })
    expect(attacksApi.saveConversation).not.toHaveBeenCalled()
    expect(result.current.saving).toBe(false)
    rerender({ generation: 'gen-2', ready: true, labels: { team: 'new' }, operationId: 'new-id' })
    await act(async () => { await result.current.save(input, 'new_attack') })
    expect(attacksApi.saveConversation).toHaveBeenCalledWith(expect.objectContaining({
      operation_id: 'new-id', labels: { team: 'new' },
    }))
  })

  it('uses current labels and Operation rather than those captured when editing began', async () => {
    jest.mocked(attacksApi.saveConversation).mockResolvedValue(response)
    const { result } = renderHook(() => useConversationSave({
      generation: 'gen-2', ready: true, labels: { operator: 'owner', team: 'refreshed' }, operationId: 'refreshed-id',
    }))
    await act(async () => { await result.current.save({ ...input, labels: { team: 'old' } }, 'new_attack') })
    expect(attacksApi.saveConversation).toHaveBeenCalledWith(expect.objectContaining({
      operator: 'owner', operation_id: 'refreshed-id', labels: { operator: 'owner', team: 'refreshed' },
    }))
  })
})
