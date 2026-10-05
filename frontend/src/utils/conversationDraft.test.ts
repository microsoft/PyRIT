import type { ConversationDraftMessage, TargetCapabilities } from '@/types'
import { makeTarget } from '@/test-utils/targetFixtures'

import { draftConverterInputs, draftDataTypes, editorTargetDisabledReason, serializeDraft, toConversationDraft, unansweredToolCallId, validateDraft } from './conversationDraft'

describe('conversation drafts', () => {
  it.each([
    ['assistant', 'simulated_assistant'],
    ['tool', 'simulated_tool'],
    ['simulated_tool', 'simulated_tool'],
  ])('keeps %s response provenance synthetic in saved drafts', async (role, expectedRole) => {
    const draft = toConversationDraft([{
      role, turn_number: 0, created_at: '2026-01-01T00:00:00Z',
      message_pieces: [{
        id: 'source-piece', original_value_data_type: 'text', converted_value_data_type: 'text',
        original_value: 'Recorded response', converted_value: 'Recorded response', scores: [], response_error: 'none',
      }],
    }])
    expect(draft[0].role).toBe(expectedRole)
    const saved = await serializeDraft(draft)
    expect(saved[0].role).toBe(expectedRole)
    expect(saved[0].pieces[0].source_piece_id).toBe('source-piece')
  })

  it('omits converter provenance after editing or clearing the converted value', async () => {
    const payload = await serializeDraft([{
      id: 'edited', role: 'user', pieces: [{
        draftId: 'piece', data_type: 'text', original_value: 'changed', applied_converter_ids: [],
      }],
    }])
    expect(payload[0].pieces[0].applied_converter_ids).toBeUndefined()
    expect(JSON.stringify(payload)).not.toContain('applied_converter_ids')
  })

  const messages: ConversationDraftMessage[] = [{
    id: 'message', role: 'simulated_assistant', pieces: [{
      draftId: 'call', data_type: 'function_call',
      original_value: JSON.stringify({ id: 'call-1', function: { name: 'lookup', arguments: '{}' } }),
    }],
  }, {
    id: 'output', role: 'simulated_tool', pieces: [{
      draftId: 'response', data_type: 'function_call_output',
      original_value: JSON.stringify({ call_id: 'call-1', output: 'answer' }),
    }],
  }]

  it('validates both nested and flat function calls without running them', () => {
    expect(validateDraft(messages)).toBeNull()
    expect(validateDraft([{
      ...messages[0], pieces: [{
        ...messages[0].pieces[0], original_value: JSON.stringify({ call_id: 'call-1', name: 'lookup', arguments: '{}' }),
      }],
    }, messages[1]])).toBeNull()
  })

  it('reports broken links and does not pass tool envelopes to text converters', () => {
    expect(validateDraft([messages[1]])).toMatch(/preceding, unanswered call/)
    expect(validateDraft([messages[0], messages[0]])).toMatch(/Duplicate tool call/)
    expect(draftConverterInputs(messages, new Set(['message', 'output']))).toEqual([])
  })

  it('derives requirements from all current effective pieces and keeps tool links', () => {
    expect(draftDataTypes(messages)).toEqual(['function_call', 'function_call_output'])
    expect(unansweredToolCallId(messages, 'message')).toBeUndefined()
    expect(unansweredToolCallId([messages[0]], 'message')).toBe('call-1')
    expect(draftDataTypes([{
      ...messages[0], pieces: [{ ...messages[0].pieces[0], converted_value: 'text', converted_value_data_type: 'text' }],
    }])).toEqual(['text'])
    expect(draftDataTypes([])).toEqual([])
  })

  it.each(['audio_path', 'video_path', 'binary_path'])('checks %s anywhere in the effective draft', (dataType: string) => {
    const target = makeTarget({
      target_registry_name: 'text',
      capabilities: {
        supports_multi_turn: true, supports_editable_history: true,
        supported_input_modalities: ['text'],
      },
    })
    const history: ConversationDraftMessage[] = [{
      id: 'media', role: 'user', pieces: [{
        draftId: 'piece', data_type: 'text', original_value: 'original',
        converted_value: 'media', converted_value_data_type: dataType,
      }],
    }, {
      id: 'reply', role: 'simulated_assistant',
      pieces: [{ draftId: 'reply-piece', data_type: 'text', original_value: 'reply' }],
    }]
    expect(draftDataTypes(history)).toEqual([dataType, 'text'])
    expect(editorTargetDisabledReason(target, draftDataTypes(history))).toContain(dataType)
    expect(editorTargetDisabledReason(target, draftDataTypes(history.slice(1)))).toBeUndefined()
    history[0].pieces[0] = {
      draftId: 'piece', data_type: dataType, original_value: 'media',
      converted_value: 'transcript', converted_value_data_type: 'text',
    }
    expect(draftDataTypes(history)).toEqual(['text'])
    expect(editorTargetDisabledReason(target, draftDataTypes(history))).toBeUndefined()
  })

  it('requires editable multi-turn history and each input modality', () => {
    const capabilities: TargetCapabilities = {
      supports_multi_turn: true, supports_editable_history: true, supports_json_schema: false,
      supports_json_output: false, supports_system_prompt: true,
      supported_input_modalities: ['text', 'function_call'], supported_output_modalities: ['text'],
    }
    const target = makeTarget({ target_registry_name: 'chat', capabilities })
    expect(editorTargetDisabledReason(target, [])).toBeUndefined()
    expect(editorTargetDisabledReason(target, ['function_call'])).toBeUndefined()
    expect(editorTargetDisabledReason(target, ['function_call_output'])).toMatch(/function_call_output/)
    expect(editorTargetDisabledReason(makeTarget({ target_registry_name: 'unknown' }), [])).toMatch(/editable history/)
    expect(editorTargetDisabledReason({
      ...target, capabilities: { ...capabilities, supports_multi_turn: false },
    }, [])).toMatch(/editable history/)
  })

  it('does not collapse the identities of multiple text pieces', () => {
    const multi: ConversationDraftMessage[] = [{
      id: 'message', role: 'user', pieces: [
        { draftId: 'first', data_type: 'text', original_value: 'one' },
        { draftId: 'second', data_type: 'text', original_value: 'two' },
      ],
    }]
    expect(draftConverterInputs(multi, new Set(['message'])).map((piece) => piece.id)).toEqual(['first', 'second'])
  })
})
