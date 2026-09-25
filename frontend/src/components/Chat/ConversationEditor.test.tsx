import { useEffect } from 'react'

import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { createMemoryRouter, RouterProvider } from 'react-router'

import { attacksApi, convertersApi } from '@/services/api'
import { useConversationDraft } from '@/hooks/useConversationDraft'
import type { AddMessageResponse, BackendMessage, ConversationDraftMessage, TargetInstance } from '@/types'
import { toConversationDraft } from '@/utils/conversationDraft'

import ConversationEditor from './ConversationEditor'

jest.mock('@/services/api', () => ({
  attacksApi: { saveConversation: jest.fn() },
  convertersApi: { listConverters: jest.fn(), listConverterTypes: jest.fn(), previewConversion: jest.fn() },
}))

const source: BackendMessage[] = [{
  turn_number: 0, role: 'user', created_at: '2026-01-01T00:00:00Z',
  message_pieces: [
    { id: 'source-1', original_value_data_type: 'text', original_value: 'First piece',
      converted_value_data_type: 'text', converted_value: 'First piece', scores: [], response_error: 'none' },
    { id: 'source-2', original_value_data_type: 'text', original_value: 'Second piece',
      converted_value_data_type: 'text', converted_value: 'Converted second', scores: [], response_error: 'none' },
  ],
}, {
  turn_number: 1, role: 'assistant', created_at: '2026-01-01T00:00:01Z',
  message_pieces: [
    { id: 'source-3', original_value_data_type: 'text', original_value: 'Reply',
      converted_value_data_type: 'text', converted_value: 'Reply', scores: [], response_error: 'none' },
  ],
}]

function setupEditor(messages: ConversationDraftMessage[] = toConversationDraft(source), disabledReason?: string, target?: TargetInstance) {
  const onSaved = jest.fn()
  function EditorHarness() {
    const controller = useConversationDraft()
    const { begin } = controller
    useEffect(() => {
      begin({
        messages, initialObjective: 'Objective', objective: 'Objective', target: target ?? null,
        sourceAttackId: 'source-attack', sourceConversationId: 'source-conversation', labels: { operator: 'owner' },
      })
    }, [begin])
    return controller.draft && <ConversationEditor controller={controller}
      sameAttackDisabledReason={disabledReason} onSaved={onSaved} />
  }
  const router = createMemoryRouter([{
    path: '/',
    element: <EditorHarness />,
  }, { path: '/next', element: <p>Next page</p> }])
  const { unmount } = render(<FluentProvider theme={webLightTheme}><RouterProvider router={router} /></FluentProvider>)
  return { onSaved, router, unmount }
}

describe('ConversationEditor', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    jest.mocked(attacksApi.saveConversation).mockImplementation(() => new Promise(() => {}))
    jest.mocked(convertersApi.listConverters).mockResolvedValue({ items: [] })
    jest.mocked(convertersApi.listConverterTypes).mockResolvedValue({ items: [] })
  })

  it('saves the active editor without Done and preserves piece order and source references', async () => {
    const user = userEvent.setup()
    setupEditor()
    const first = screen.getByRole('region', { name: 'Message 1' })
    const text = within(first).getAllByPlaceholderText('Type prompt here')[0]
    await user.clear(text)
    await user.type(text, 'Changed{Enter}line')
    expect(screen.queryByRole('button', { name: 'Send message' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Save conversation' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Save conversation' }))
    await waitFor(() => expect(attacksApi.saveConversation).toHaveBeenCalledTimes(1))
    const request = jest.mocked(attacksApi.saveConversation).mock.calls[0][0]
    expect(request.messages[0].pieces.map((piece) => piece.original_value)).toEqual(['Changed\nline', 'Second piece'])
    expect(request.messages[0].pieces[0].source_piece_id).toBe('source-1')
    expect(request.messages[0].pieces[1].converted_value).toBe('Converted second')
    expect(request.messages[1].role).toBe('simulated_assistant')
    expect(source[0].message_pieces[0].original_value).toBe('First piece')
  })

  it('switches from one-message stage view to batch final-only view through chat selection', async () => {
    const user = userEvent.setup()
    setupEditor()
    await user.click(within(screen.getByRole('region', { name: 'Message 1' })).getAllByRole('button', { name: 'Toggle converter panel' })[0])
    expect(await screen.findByText('Select messages in chat.')).toBeInTheDocument()
    expect(screen.getAllByTestId('converter-input-value')).toHaveLength(2)
    await user.click(screen.getByRole('checkbox', { name: 'Select message 2' }))
    expect(screen.queryByText(/^\d+ selected$/)).not.toBeInTheDocument()
    expect(screen.queryAllByTestId('converter-input-value')).toHaveLength(0)
    await user.click(within(screen.getByRole('region', { name: 'Message 2' })).getByRole('button', { name: 'Toggle converter panel' }))
    expect(screen.getByRole('checkbox', { name: 'Select message 1' })).not.toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Select message 2' })).toBeChecked()
    expect(screen.getAllByTestId('converter-input-value')).toHaveLength(1)
  })

  it('disables Same attack with a focusable reason and permits a targetless new attack', async () => {
    const user = userEvent.setup()
    setupEditor([], 'This attack belongs to another operator.')
    await user.click(screen.getByRole('button', { name: 'Save conversation' }))
    expect(screen.getByRole('radio', { name: 'Same attack' })).toBeDisabled()
    expect(screen.getByLabelText('This attack belongs to another operator.')).toHaveAttribute('tabindex', '0')
    expect(screen.getByRole('radio', { name: 'New attack' })).toBeChecked()
    expect(screen.queryByText('A target is optional. Select one in chat before sending.')).not.toBeInTheDocument()
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Save conversation' }))
    await waitFor(() => expect(attacksApi.saveConversation).toHaveBeenCalledTimes(1))
    expect(jest.mocked(attacksApi.saveConversation).mock.calls[0][0]).toMatchObject({
      destination: 'new_attack', objective: 'Objective', messages: [],
    })
    expect(jest.mocked(attacksApi.saveConversation).mock.calls[0][0].target_registry_name).toBeUndefined()
  })

  it('inserts and removes messages with roles inside the prompt and no edit toolbar', async () => {
    const user = userEvent.setup()
    setupEditor()
    expect(screen.queryByRole('textbox', { name: 'Draft objective' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Edit message' })).not.toBeInTheDocument()
    expect(screen.queryByText('The original messages stay unchanged.')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Insert message after message 1' }))
    const inserted = screen.getByRole('region', { name: 'Message 2' })
    expect(within(inserted).getAllByRole('option').map((option: HTMLElement) => option.textContent))
      .toEqual(['system', 'developer', 'user', 'simulated_assistant'])
    await user.selectOptions(within(inserted).getByRole('combobox', { name: 'Role for message 2' }), 'system')
    await user.type(within(inserted).getByPlaceholderText('Type prompt here'), 'Inserted context')
    await user.click(screen.getByRole('button', { name: 'Delete message 3' }))
    await user.click(screen.getByRole('button', { name: 'Save conversation' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Save conversation' }))
    await waitFor(() => expect(attacksApi.saveConversation).toHaveBeenCalledTimes(1))
    const { messages } = jest.mocked(attacksApi.saveConversation).mock.calls[0][0]
    expect(messages).toHaveLength(2)
    expect(messages[1]).toMatchObject({ role: 'system', pieces: [{ original_value: 'Inserted context' }] })
  })

  it('keeps tool editing available in the content menu', async () => {
    const user = userEvent.setup()
    setupEditor()
    await user.click(within(screen.getByRole('region', { name: 'Message 2' })).getByRole('button', { name: 'Add content' }))
    await user.click(screen.getByRole('menuitem', { name: 'Add tool call' }))
    await user.type(screen.getByRole('textbox', { name: 'call_id' }), 'call-1')
    await user.type(screen.getByRole('textbox', { name: 'name' }), 'test_tool')
    expect(screen.getByRole('textbox', { name: 'Raw function_call content' })).toHaveValue(
      '{"type":"function_call","call_id":"call-1","name":"test_tool","arguments":"{}"}',
    )
    expect(screen.getByRole('button', { name: 'Save conversation' })).toBeEnabled()
  })

  it('creates a linked response with the tool role and saves text after deleting both tool pieces', async () => {
    const user = userEvent.setup()
    setupEditor()
    const assistant = screen.getByRole('region', { name: 'Message 2' })
    await user.click(within(assistant).getByRole('button', { name: 'Add content' }))
    expect(screen.getByRole('menuitem', { name: 'Add tool response' })).toHaveAttribute('aria-disabled', 'true')
    await user.click(screen.getByRole('menuitem', { name: 'Add tool call' }))
    await user.type(screen.getByRole('textbox', { name: 'call_id' }), 'call-1')
    await user.type(screen.getByRole('textbox', { name: 'name' }), 'lookup')
    await user.click(within(assistant).getAllByRole('button', { name: 'Add content' })[0])
    await user.click(screen.getByRole('menuitem', { name: 'Add tool response' }))
    const response = screen.getByRole('region', { name: 'Message 3' })
    expect(within(response).getByRole('combobox')).toHaveValue('tool')
    expect(within(response).getByRole('textbox', { name: 'call_id' })).toHaveValue('call-1')
    await user.type(within(response).getByRole('textbox', { name: 'output' }), 'answer')
    await user.click(within(assistant).getAllByRole('button', { name: 'Add content' })[0])
    expect(screen.getByRole('menuitem', { name: 'Add tool response' })).toHaveAttribute('aria-disabled', 'true')
    await user.keyboard('{Escape}')
    await user.click(screen.getByRole('button', { name: 'Remove piece 2 from message 2' }))
    expect(screen.getByRole('button', { name: 'Save conversation' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Delete message 3' }))
    await user.click(screen.getByRole('button', { name: 'Save conversation' }))
    await user.click(screen.getByRole('radio', { name: 'New attack' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Save conversation' }))
    await waitFor(() => expect(attacksApi.saveConversation).toHaveBeenCalledTimes(1))
    const request = jest.mocked(attacksApi.saveConversation).mock.calls[0][0]
    expect(request.destination).toBe('new_attack')
    expect(request.messages[1]).toMatchObject({ role: 'simulated_assistant', pieces: [{ original_value: 'Reply' }] })
    expect(request.messages.flatMap((message) => message.pieces).map((piece) => piece.data_type))
      .toEqual(['text', 'text', 'text'])
  })

  it('preserves developer and tool-only source messages and can attach a linked response', async () => {
    const user = userEvent.setup()
    setupEditor([{
      id: 'developer', role: 'developer', pieces: [{ draftId: 'instruction', data_type: 'text', original_value: 'Instruction' }],
    }, {
      id: 'call', role: 'simulated_assistant', pieces: [{
        draftId: 'tool', data_type: 'function_call',
        original_value: '{"id":"call-1","function":{"name":"lookup","arguments":"{}"}}',
      }],
    }])
    expect(screen.getByRole('combobox', { name: 'Role for message 1' })).toHaveValue('developer')
    await user.click(within(screen.getByRole('region', { name: 'Message 2' })).getByRole('button', { name: 'Add content' }))
    await user.click(screen.getByRole('menuitem', { name: 'Add tool response' }))
    expect(screen.getByRole('combobox', { name: 'Role for message 3' })).toHaveValue('tool')
    expect(screen.getByRole('button', { name: 'Save conversation' })).toBeEnabled()
  })

  it('keeps the draft and reuses the save identity after a failed request', async () => {
    const user = userEvent.setup()
    jest.mocked(attacksApi.saveConversation).mockRejectedValue(new Error('Connection lost'))
    setupEditor()
    await user.click(screen.getByRole('button', { name: 'Save conversation' }))
    const save = within(screen.getByRole('dialog')).getByRole('button', { name: 'Save conversation' })
    await user.click(save)
    expect(await screen.findByText('Connection lost')).toBeInTheDocument()
    await user.click(save)
    await waitFor(() => expect(attacksApi.saveConversation).toHaveBeenCalledTimes(2))
    const calls = jest.mocked(attacksApi.saveConversation).mock.calls
    expect(calls[0][0].save_id).toBe(calls[1][0].save_id)
    expect(screen.getByRole('region', { name: 'Message 1', hidden: true })).toBeInTheDocument()
  })

  it('blocks navigation from a changed draft and can keep editing', async () => {
    const user = userEvent.setup()
    const { router } = setupEditor()
    await user.type(screen.getAllByPlaceholderText('Type prompt here')[0], ' changed')
    await act(async () => { await router.navigate('/next') })
    expect(screen.getByRole('dialog', { name: 'Discard conversation draft?' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Keep editing' }))
    expect((await screen.findAllByPlaceholderText('Type prompt here'))[0]).toHaveValue('First piece changed')
    expect(screen.queryByText('Next page')).not.toBeInTheDocument()
  })

  it('uploads a file as a separate ordered piece without persisting its object URL', async () => {
    const user = userEvent.setup()
    setupEditor()
    const first = screen.getByRole('region', { name: 'Message 1' })
    const file = new File(['file bytes'], 'sample.txt', { type: 'text/plain' })
    await user.upload(within(first).getAllByTestId('file-input')[0], file)
    expect(within(first).getAllByText(/sample\.txt/)).toHaveLength(1)
    await user.click(screen.getByRole('button', { name: 'Save conversation' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Save conversation' }))
    await waitFor(() => expect(attacksApi.saveConversation).toHaveBeenCalledTimes(1))
    const pieces = jest.mocked(attacksApi.saveConversation).mock.calls[0][0].messages[0].pieces
    expect(pieces).toHaveLength(3)
    expect(pieces[2].original_value).toBe(btoa('file bytes'))
    expect(pieces[2].data_type).toBe('binary_path')
  })

  it('does not navigate after a save completes on an unmounted editor', async () => {
    const user = userEvent.setup()
    let complete: (response: AddMessageResponse) => void = () => {}
    jest.mocked(attacksApi.saveConversation).mockImplementation(() => new Promise((resolve) => { complete = resolve }))
    const { onSaved, unmount } = setupEditor()
    await user.click(screen.getByRole('button', { name: 'Save conversation' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Save conversation' }))
    await waitFor(() => expect(attacksApi.saveConversation).toHaveBeenCalledTimes(1))
    unmount()
    await act(async () => {
      complete({
        attack: {
          attack_result_id: 'source-attack', conversation_id: 'source-conversation', attack_type: 'ManualAttack',
          objective: 'Objective', converters: [], message_count: 2, related_conversation_ids: ['saved'], labels: {},
          created_at: '', updated_at: '',
        },
        messages: { conversation_id: 'saved', messages: source, target_response_status: null },
      })
    })
    expect(onSaved).not.toHaveBeenCalled()
  })
})
