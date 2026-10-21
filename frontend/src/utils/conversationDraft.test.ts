import type { ChatConverterSnapshot, ConversationDraftMessage, TargetCapabilities } from '@/types'
import { makeTarget } from '@/test-utils/targetFixtures'

import { buildMessagePieces } from './messageMapper'
import {
  draftConverterInputs, draftDataTypes, draftMessageToComposer, draftMessageToInput, editorTargetDisabledReason,
  historyReplayDisabledReason, serializeDraft, toConversationDraft, unansweredToolCallId, validateDraft,
} from './conversationDraft'

describe('conversation drafts', () => {
  it('returns original text and media with separate conversions and remapped converter stages', () => {
    const stage = { id: 'stage', converterId: 'base64' }
    const snapshot: ChatConverterSnapshot = {
      generation: 'runtime', pipelines: { text: [stage], image: [{ id: 'image-stage', converterId: 'image' }] },
      workingInputs: { 'pending-text': 'Working text', unrelated: 'Other message' },
      stageResults: { 'pending-text': [{
        stageId: stage.id, value: 'Edited output', generated: {
          converter_id: 'base64', converter_type: 'Base64Converter', input_value: 'Working text',
          input_data_type: 'text', output_value: 'Generated output', output_data_type: 'text',
        },
      }] },
      errors: { unrelated: 'Other error' }, applied: {},
    }
    const file = new File(['image'], 'original.png', { type: 'image/png' })
    const prompt = draftMessageToComposer({
      id: 'pending', role: 'user', pieces: [{
        draftId: 'pending-text', data_type: 'text', original_value: 'Original text',
        converted_value: 'Edited output', converted_value_data_type: 'text', applied_converter_ids: ['base64'],
        applied_converter_provenance: ['signed-text'],
        prompt_metadata: { video_id: 'video' },
      }, {
        draftId: 'image', data_type: 'image_path', original_value: '', file, previewUrl: 'blob:original',
        converted_value: 'converted.png', converted_value_data_type: 'image_path', applied_converter_ids: ['image'],
        applied_converter_provenance: ['signed-image'],
      }],
    }, snapshot)
    expect(prompt.text).toBe('Original text')
    expect(prompt.textMetadata).toEqual({ video_id: 'video' })
    expect(prompt.attachments[0]).toMatchObject({ draftId: 'image', file, url: 'blob:original' })
    expect(prompt.converters.applied.text).toMatchObject({
      pieceId: 'text', originalValue: 'Original text', convertedValue: 'Edited output', converterInstanceIds: ['base64'],
      converterProvenance: ['signed-text'],
    })
    expect(prompt.converters.applied.image).toMatchObject({
      pieceId: 'image', convertedValue: 'converted.png', converterInstanceIds: ['image'],
      converterProvenance: ['signed-image'],
    })
    expect(prompt.converters.stageResults.text).toEqual(snapshot.stageResults['pending-text'])
    expect(prompt.converters.workingInputs).toEqual({ text: 'Working text' })
    expect(prompt.converters.errors).toEqual({})
    expect(prompt.converters.pipelines).toEqual(snapshot.pipelines)
  })

  it('keeps a text-to-file conversion separate from its original prompt', () => {
    const prompt = draftMessageToComposer({
      id: 'pending', role: 'user', pieces: [{
        draftId: 'pending-text', data_type: 'text', original_value: 'Make an image',
        converted_value: 'output.png', converted_value_data_type: 'image_path', applied_converter_ids: ['draw'],
      }],
    }, { generation: 'runtime', pipelines: {}, stageResults: {}, workingInputs: {}, errors: {}, applied: {} })
    expect(prompt.text).toBe('Make an image')
    expect(prompt.attachments).toEqual([])
    expect(prompt.converters.applied.text).toMatchObject({
      convertedValue: 'output.png', convertedDataType: 'image_path', originalValue: 'Make an image',
    })
  })

  it('combines text pieces without replacing their originals with converted text or clearing pipelines', () => {
    const pipelines = { text: [{ id: 'stage', converterId: 'base64' }] }
    const prompt = draftMessageToComposer({
      id: 'pending', role: 'user', pieces: [{
        draftId: 'one', data_type: 'text', original_value: 'First', converted_value: 'Converted first',
      }, { draftId: 'two', data_type: 'text', original_value: 'Second' }],
    }, { generation: 'runtime', pipelines, stageResults: {}, workingInputs: {}, errors: {}, applied: {} })
    expect(prompt.text).toBe('First\nSecond')
    expect(prompt.converters.applied.text.convertedValue).toBe('Converted first\nSecond')
    expect(prompt.converters.applied.text.manuallyEdited).toBe(true)
    expect(prompt.converters.pipelines).toEqual(pipelines)
    expect(prompt.notice).toContain('Multiple text pieces')
  })

  it.each([
    [false, false], [true, false], [true, true],
  ])('allows a fresh request for multi-turn=%s and editable=%s', (multiTurn: boolean, editable: boolean) => {
    const target = makeTarget({
      target_registry_name: 'fresh',
      capabilities: { supports_multi_turn: multiTurn, supports_editable_history: editable, supported_input_modalities: ['text'] },
    })
    expect(historyReplayDisabledReason(target, [])).toBeUndefined()
    expect(editorTargetDisabledReason(target, ['text'], [])).toBeUndefined()
    expect(editorTargetDisabledReason(target, ['image_path'], [])).toMatch(/image_path/)
  })

  it.each(['user', 'assistant', 'simulated_assistant', 'tool', 'simulated_tool'])(
    'rejects replayed %s history without editable history, but allows an editable target', (role: string) => {
      const target = makeTarget({
        target_registry_name: 'immutable',
        capabilities: { supports_multi_turn: true, supports_editable_history: false, supported_input_modalities: ['text'] },
      })
      expect(historyReplayDisabledReason(target, [{ role }])).toMatch(/editable history/)
      const editable = makeTarget({
        target_registry_name: 'editable',
        capabilities: { supports_multi_turn: true, supports_editable_history: true, supported_input_modalities: ['text'] },
      })
      expect(historyReplayDisabledReason(editable, [{ role }])).toBeUndefined()
    },
  )

  it.each([false, true])('uses effective replay support for an adapted single-turn=%s target', (singleTurn: boolean) => {
    const target = makeTarget({
      target_registry_name: 'adapted',
      supports_history_replay: true,
      capabilities: {
        supports_multi_turn: !singleTurn, supports_editable_history: false, supported_input_modalities: ['text'],
      },
    })
    const history = [{ role: 'user' }, { role: 'assistant' }]
    expect(historyReplayDisabledReason(target, history)).toBeUndefined()
    expect(editorTargetDisabledReason(target, ['text'], history)).toBeUndefined()
    expect(historyReplayDisabledReason({ ...target, supports_history_replay: false }, history)).toBeDefined()
    expect(editorTargetDisabledReason(target, ['image_path'], history)).toMatch(/image_path/)
  })

  it('allows initial system setup only with system-prompt and multi-turn support', () => {
    const history = [{ role: 'system' }, { role: 'developer' }]
    const target = makeTarget({
      target_registry_name: 'system-bootstrap',
      capabilities: { supports_multi_turn: true, supports_editable_history: false, supports_system_prompt: true },
    })
    expect(historyReplayDisabledReason(target, history)).toBeUndefined()
    expect(historyReplayDisabledReason(target, [...history, { role: 'simulated_assistant' }])).toBeDefined()
    expect(historyReplayDisabledReason(makeTarget({
      target_registry_name: 'no-system',
      capabilities: { supports_multi_turn: true, supports_editable_history: false, supports_system_prompt: false },
    }), history)).toBeDefined()
  })

  it('preserves text-only remix metadata through composer restoration and request construction', async () => {
    const prompt = draftMessageToInput({
      id: 'user', role: 'user', pieces: [{
        draftId: 'text', data_type: 'text', original_value: 'Change the background',
        prompt_metadata: { video_id: 'existing-video', custom: 'preserved' },
      }],
    })
    expect(prompt.attachments).toEqual([])
    expect(await buildMessagePieces(prompt.text, prompt.attachments, prompt.textMetadata)).toEqual([{
      data_type: 'text', original_value: 'Change the background',
      prompt_metadata: { video_id: 'existing-video', custom: 'preserved' },
    }])
  })

  it('moves text and media to the composer without losing converted content or local files', () => {
    const file = new File(['image'], 'image.png', { type: 'image/png' })
    const prompt = draftMessageToInput({
      id: 'user', role: 'user', pieces: [
        { draftId: 'text', data_type: 'text', original_value: 'Original', converted_value: 'Converted' },
        { draftId: 'file', data_type: 'image_path', original_value: '', file, previewUrl: 'blob:local' },
        { draftId: 'converted', data_type: 'text', original_value: 'Image prompt',
          converted_value: 'output.png', converted_value_data_type: 'image_path' },
      ],
    })
    expect(prompt.text).toBe('Converted')
    expect(prompt.attachments).toHaveLength(2)
    expect(prompt.attachments[0]).toMatchObject({ file, url: 'blob:local' })
    expect(prompt.attachments[1]).toMatchObject({ sourceValue: 'output.png', sourceDataType: 'image_path' })
  })

  it.each([false, true])('preserves video remix metadata and source identity when moving a prompt with conversion: %s', async (converted: boolean) => {
    const prompt = draftMessageToInput({
      id: 'user', role: 'user', pieces: [
        { draftId: 'text', data_type: 'text', original_value: 'Remix this video' },
        {
          draftId: 'video', data_type: 'video_path', original_value: 'video.mp4',
          source_piece_id: 'video-source', prompt_metadata: { video_id: 'video-123' },
          ...(converted ? { converted_value: 'video.mp4', converted_value_data_type: 'video_path' } : {}),
        },
      ],
    })
    const pieces = await buildMessagePieces(prompt.text, prompt.attachments)
    expect(pieces).toEqual([
      {
        data_type: 'text', original_value: 'Remix this video',
        prompt_metadata: { video_id: 'video-123' },
      },
      expect.objectContaining({
        data_type: 'video_path', original_value: 'video.mp4', original_prompt_id: 'video-source',
        prompt_metadata: { video_id: 'video-123' },
      }),
    ])
  })

  it.each(['image_path', 'video_path'])('does not assign an original source identity to converted %s content', async (dataType: string) => {
    const prompt = draftMessageToInput({
      id: 'user', role: 'user', pieces: [{
        draftId: 'media', source_piece_id: 'original-source', data_type: 'image_path',
        original_value: 'original.png', converted_value: 'converted-media',
        converted_value_data_type: dataType,
      }],
    })
    const [piece] = await buildMessagePieces(prompt.text, prompt.attachments)
    expect(piece.data_type).toBe(dataType)
    expect(piece.original_value).toBe('converted-media')
    expect(piece.original_prompt_id).toBeUndefined()
  })

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
