import type { ConversationDraftMessage, TargetCapabilities } from '@/types'
import { makeTarget } from '@/test-utils/targetFixtures'

import { draftConverterInputs, draftToolTypes, editorTargetDisabledReason, serializeDraft, unansweredToolCallId, validateDraft } from './conversationDraft'

describe('conversation drafts', () => {
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
    id: 'output', role: 'tool', pieces: [{
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

  it('derives tool requirements only from current effective pieces and links', () => {
    expect(draftToolTypes(messages)).toEqual(['function_call', 'function_call_output'])
    expect(unansweredToolCallId(messages, 'message')).toBeUndefined()
    expect(unansweredToolCallId([messages[0]], 'message')).toBe('call-1')
    expect(draftToolTypes([{
      ...messages[0], pieces: [{ ...messages[0].pieces[0], converted_value: 'text', converted_value_data_type: 'text' }],
    }])).toEqual([])
    expect(draftToolTypes([])).toEqual([])
  })

  it('requires editable multi-turn history and each tool input modality', () => {
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
