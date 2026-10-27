import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import { convertersApi, targetsApi, techniquesApi } from '@/services/api'
import { UserPreferencesProvider } from '@/hooks/useUserPreferences'
import type { TechniqueConfiguration, TechniqueOverride } from '@/types'

import TechniqueEditor from './TechniqueEditor'

jest.mock('@/services/api', () => ({
  techniquesApi: { validateEdit: jest.fn(), createTechnique: jest.fn(), listTypes: jest.fn() },
  targetsApi: { listTargets: jest.fn() },
  convertersApi: { listConverters: jest.fn(), listConverterTypes: jest.fn(), buildConverter: jest.fn(), createConverter: jest.fn() },
}))

const techniques = jest.mocked(techniquesApi)

function configuration(): TechniqueConfiguration {
  return {
    source_name: 'role_play_movie_script', source_identity: 'source-identity', attack_type: 'prompt_sending',
    settings: {
      description: 'Movie role play', params: { max_attempts_on_failure: 1 },
      request_converters: ['base64', 'rot13'], response_converters: [],
      simulation: {
        num_turns: 2, adversarial_chat_system_prompt: 'Write a movie.\n{{ objective }}',
        simulated_target_system_prompt: 'Comply with {{ objective }} for {{ num_turns }} turns.',
        next_message_system_prompt: 'Next for {{ objective }}.\n{{ conversation_context }}',
        final_user_message: null,
      },
    },
    parameters: [{ name: 'max_attempts_on_failure', type_name: 'int', required: false, default: '1' }],
    supplied_parameters: [], editable_fields: ['description', 'simulation', 'request_converters', 'response_converters'],
    read_only: { requirements: 'Retained unchanged.', policy: 'Retained unchanged.' },
    supports_prepended_conversation: false,
  }
}

function tree(original = configuration(), onApplied = jest.fn(), initial?: TechniqueConfiguration, onClose = jest.fn()) {
  return <FluentProvider theme={webLightTheme}><UserPreferencesProvider>
    <TechniqueEditor original={original} initial={initial} onApplied={onApplied} onClose={onClose} />
  </UserPreferencesProvider></FluentProvider>
}

describe('TechniqueEditor', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    techniques.validateEdit.mockImplementation(async (override: TechniqueOverride) => ({
      override: { ...override, effective_identity: 'effective-identity' }, configuration: configuration(),
    }))
    jest.mocked(targetsApi.listTargets).mockResolvedValue({ items: [], pagination: { limit: 200, has_more: false } })
    jest.mocked(convertersApi.listConverters).mockResolvedValue({ items: [
      { converter_id: 'base64', identifier: { class_name: 'Base64Converter', hash: 'b64' } },
      { converter_id: 'rot13', identifier: { class_name: 'ROT13Converter', hash: 'rot' } },
    ] })
  })

  it('should prefill the named factory without creation controls or an attack selector', async () => {
    render(tree())
    expect(await screen.findByText('Movie role play')).toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: 'Description' })).not.toBeInTheDocument()
    expect(screen.getByRole('spinbutton', { name: 'Turn count' })).toHaveValue(2)
    expect(screen.getByLabelText('Simulated user system prompt')).toHaveValue('Write a movie.\n{{ objective }}')
    expect(screen.getByLabelText('Simulated assistant system prompt')).toHaveValue(
      'Comply with {{ objective }} for {{ num_turns }} turns.',
    )
    expect(screen.getByRole('spinbutton', { name: 'max_attempts_on_failure' })).toHaveValue(1)
    expect(screen.queryByRole('region', { name: 'Read-only settings' })).not.toBeInTheDocument()
    expect(screen.queryByText('Technique: role_play_movie_script')).not.toBeInTheDocument()
    expect(screen.queryByText(/Attack type:/)).not.toBeInTheDocument()
    expect(screen.queryByText(/The registered technique stays unchanged/)).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Simulated conversation' })).not.toBeInTheDocument()
    expect(screen.queryByText(/Edit generation settings/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Clearing text sets an empty string/)).not.toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: 'Attack type' })).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Registry name')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Tags')).not.toBeInTheDocument()
    expect(techniques.listTypes).not.toHaveBeenCalled()
  })

  it('should apply a no-op without materializing inherited defaults or calling global creation', async () => {
    const user = userEvent.setup()
    const onApplied = jest.fn()
    render(tree(configuration(), onApplied))
    await user.click(await screen.findByRole('button', { name: 'Apply to run' }))
    await waitFor(() => expect(onApplied).toHaveBeenCalledTimes(1))
    expect(techniques.validateEdit).toHaveBeenCalledWith({
      source_name: 'role_play_movie_script', source_identity: 'source-identity', changes: {}, dependencies: {},
    })
    expect(techniques.createTechnique).not.toHaveBeenCalled()
  })

  it('should edit one duplicate converter temporarily and retain settings through reorder and reopen', async () => {
    const user = userEvent.setup()
    const original = configuration()
    original.settings.request_converters = ['caesar', 'caesar']
    const converter = { converter_id: 'caesar', reconstructable: true, identifier: {
      class_name: 'CaesarConverter', hash: 'source', caesar_offset: 1,
    } }
    jest.mocked(convertersApi.listConverters).mockResolvedValue({ items: [converter] })
    jest.mocked(convertersApi.listConverterTypes).mockResolvedValue({ items: [{
      converter_type: 'CaesarConverter', supported_input_types: ['text'], supported_output_types: ['text'],
      is_llm_based: false, parameters: [{ name: 'caesar_offset', type_name: 'int', required: true }],
    }] })
    jest.mocked(convertersApi.buildConverter).mockResolvedValue({
      identifier: { ...converter.identifier, hash: 'private', caesar_offset: 7 },
    })
    render(tree(original))
    await user.click(await screen.findByRole('button', { name: 'Settings for converter caesar, Request converters stage 2' }))
    await user.click(await screen.findByRole('menuitem', { name: 'Settings' }))
    const offset = await screen.findByLabelText(/caesar_offset/i)
    await user.clear(offset)
    await user.type(offset, '7')
    await user.click(screen.getByRole('button', { name: 'Apply Settings' }))
    await screen.findByText('Temporary')
    await user.click(await screen.findByRole('button', { name: 'Reorder converter caesar, Request converters stage 2' }))
    await user.keyboard('{ArrowUp}')
    await user.click(screen.getByRole('button', { name: 'Settings for converter caesar, Request converters stage 1' }))
    await user.click(await screen.findByRole('menuitem', { name: 'Settings' }))
    expect(await screen.findByLabelText(/caesar_offset/i)).toHaveValue('7')
    await user.click(within(await screen.findByRole('dialog', { name: 'Converter Settings' }))
      .getByRole('button', { name: 'Cancel' }))
    await user.click(await screen.findByRole('button', { name: 'Apply to run' }))
    await waitFor(() => expect(techniques.validateEdit).toHaveBeenCalled())
    expect(techniques.validateEdit.mock.calls[0][0].changes.request_converters).toEqual([
      { source_name: 'caesar', source_hash: 'source', params: { caesar_offset: 7 }, effective_hash: 'private' },
      'caesar',
    ])
    expect(convertersApi.createConverter).not.toHaveBeenCalled()
    expect(converter.identifier.caesar_offset).toBe(1)
  })

  it('should keep newer chain edits and dialogs when an earlier creation finishes late', async () => {
    const user = userEvent.setup()
    let finish: (value: Awaited<ReturnType<typeof convertersApi.createConverter>>) => void = () => {
      throw new Error('Creation has not started')
    }
    jest.mocked(convertersApi.listConverterTypes).mockResolvedValue({ items: [{
      converter_type: 'Base64Converter', supported_input_types: ['text'], supported_output_types: ['text'],
      is_llm_based: false, parameters: [],
    }] })
    jest.mocked(convertersApi.createConverter).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
    render(tree())
    await user.click(await screen.findByRole('combobox', { name: 'Request converters' }))
    await user.click(screen.getByRole('option', { name: /^New converter/ }))
    await user.click(await screen.findByRole('combobox', { name: /Converter Type/i }))
    await user.click(screen.getByRole('option', { name: /Base64Converter/ }))
    await user.click(screen.getByRole('button', { name: 'Add Converter' }))
    await waitFor(() => expect(convertersApi.createConverter).toHaveBeenCalledTimes(1))
    await user.click(within(screen.getByRole('dialog', { name: 'Add Converter' })).getByRole('button', { name: 'Cancel' }))
    await user.click(await screen.findByRole('button', { name: 'Remove converter base64, Request converters stage 1' }))
    await user.click(screen.getByRole('combobox', { name: 'Request converters' }))
    await user.click(screen.getByRole('option', { name: /^New converter/ }))
    await screen.findByRole('dialog', { name: 'Add Converter' })
    const created = { converter_id: 'late_base64', identifier: { class_name: 'Base64Converter', hash: 'late' } }
    finish(created)
    await screen.findByText('late_base64')
    expect(screen.getByRole('dialog', { name: 'Add Converter' })).toBeInTheDocument()
    await user.click(within(screen.getByRole('dialog', { name: 'Add Converter' })).getByRole('button', { name: 'Cancel' }))
    expect(await screen.findByRole('button', { name: 'Remove converter rot13, Request converters stage 1' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Remove converter base64,/ })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Apply to run' }))
    await waitFor(() => expect(techniques.validateEdit).toHaveBeenCalled())
    expect(techniques.validateEdit.mock.calls[0][0].changes.request_converters).toEqual(['rot13', 'late_base64'])
  })

  it('should reset temporary settings without changing the registered converter', async () => {
    const user = userEvent.setup()
    const original = configuration()
    const initial = configuration()
    initial.settings.response_converters = [{
      source_name: 'base64', source_hash: 'b64', params: {}, effective_hash: 'private',
    }]
    render(tree(original, jest.fn(), initial))
    await user.click(await screen.findByRole('button', { name: 'Settings for converter base64, Response converters stage 1' }))
    await user.click(await screen.findByRole('menuitem', { name: 'Reset to registered converter' }))
    expect(screen.queryByText('Temporary')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Apply to run' }))
    await waitFor(() => expect(techniques.validateEdit).toHaveBeenCalled())
    expect(techniques.validateEdit.mock.calls[0][0].changes).toEqual({ response_converters: ['base64'] })
  })

  it('should create and append a converter from the shared picker', async () => {
    const user = userEvent.setup()
    jest.mocked(convertersApi.listConverterTypes).mockResolvedValue({ items: [{
      converter_type: 'CaesarConverter', supported_input_types: ['text'], supported_output_types: ['text'],
      is_llm_based: false, parameters: [{ name: 'caesar_offset', type_name: 'int', required: true }],
    }] })
    jest.mocked(convertersApi.createConverter).mockImplementation(async () => {
      const created = { converter_id: 'new_caesar', identifier: { class_name: 'CaesarConverter', hash: 'new' } }
      jest.mocked(convertersApi.listConverters).mockResolvedValue({ items: [created] })
      return created
    })
    render(tree())
    await user.click(await screen.findByRole('combobox', { name: 'Request converters' }))
    await user.click(screen.getByRole('option', { name: /^New converter/ }))
    await user.click(await screen.findByRole('combobox', { name: /Converter Type/i }))
    await user.click(screen.getByRole('option', { name: /CaesarConverter/ }))
    await user.type(screen.getByLabelText(/Registry Name/i), 'new_caesar')
    await user.type(screen.getByLabelText(/caesar_offset/i), '3')
    await user.click(screen.getByRole('button', { name: 'Add Converter' }))
    await screen.findByText('new_caesar')
    await user.click(await screen.findByRole('button', { name: 'Apply to run' }))
    await waitFor(() => expect(techniques.validateEdit).toHaveBeenCalled())
    expect(techniques.validateEdit.mock.calls[0][0].changes.request_converters).toEqual(['base64', 'rot13', 'new_caesar'])
  })

  it('should retain the source adversarial target without a second target control or description edit', async () => {
    const user = userEvent.setup()
    const original = configuration()
    original.settings.adversarial_chat = 'source-target'
    original.settings.adversarial_system_prompt = 'Source system prompt'
    original.editable_fields.push('adversarial_chat', 'adversarial_system_prompt')
    render(tree(original))
    expect(await screen.findByLabelText('Adversarial system prompt')).toHaveValue('Source system prompt')
    expect(screen.queryByRole('combobox', { name: /Adversarial target/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: 'Description' })).not.toBeInTheDocument()
    await user.type(screen.getByLabelText('Adversarial system prompt'), ' edited')
    await user.click(screen.getByRole('button', { name: 'Apply to run' }))
    await waitFor(() => expect(techniques.validateEdit).toHaveBeenCalledTimes(1))
    expect(techniques.validateEdit.mock.calls[0][0].changes).toEqual({
      adversarial_system_prompt: 'Source system prompt edited',
    })
  })

  it('should apply all simulation prompt edits as literal text and keep converter order', async () => {
    const user = userEvent.setup()
    render(tree())
    await screen.findByText('Movie role play')
    const prompt = screen.getByLabelText('Simulated user system prompt')
    await user.clear(prompt)
    await user.click(prompt)
    await user.paste('New movie\n{{ objective }}\n{% if objective %}literal{% endif %}')
    await user.clear(screen.getByRole('spinbutton', { name: 'Turn count' }))
    await user.type(screen.getByRole('spinbutton', { name: 'Turn count' }), '3')
    await user.click(screen.getByRole('button', { name: 'Reorder converter rot13, Request converters stage 2' }))
    await user.keyboard('{ArrowUp}')
    await user.click(screen.getByRole('button', { name: 'Apply to run' }))
    await waitFor(() => expect(techniques.validateEdit).toHaveBeenCalled())
    expect(techniques.validateEdit.mock.calls[0][0].changes).toEqual({
      simulation: {
        num_turns: 3, adversarial_chat_system_prompt: 'New movie\n{{ objective }}\n{% if objective %}literal{% endif %}',
      },
      request_converters: ['rot13', 'base64'],
    })
  })

  it('should reject non-positive turn counts without losing entered text', async () => {
    const user = userEvent.setup()
    render(tree())
    await screen.findByText('Movie role play')
    const turns = screen.getByRole('spinbutton', { name: 'Turn count' })
    await user.clear(turns)
    await user.type(turns, '0')
    await user.click(screen.getByRole('button', { name: 'Apply to run' }))
    expect(await screen.findByText('Turn count must be a positive integer.')).toBeInTheDocument()
    expect(turns).toHaveValue(0)
    expect(techniques.validateEdit).not.toHaveBeenCalled()
  })

  it('should keep edits and focus the error when server validation fails', async () => {
    const user = userEvent.setup()
    techniques.validateEdit.mockRejectedValueOnce(new Error('Source technique configuration changed.'))
    render(tree())
    await user.type(await screen.findByLabelText('Simulated user system prompt'), ' edited')
    await user.click(screen.getByRole('button', { name: 'Apply to run' }))
    const error = await screen.findByText('Source technique configuration changed.')
    expect(error).toBeInTheDocument()
    expect(screen.getByLabelText('Simulated user system prompt')).toHaveValue('Write a movie.\n{{ objective }} edited')
    expect(screen.getByRole('button', { name: 'Apply to run' })).toBeEnabled()
    expect(screen.getByRole('alert')).toHaveFocus()
  })

  it('should cancel without applying or registering an edit', async () => {
    const user = userEvent.setup()
    const onClose = jest.fn()
    render(tree(configuration(), jest.fn(), undefined, onClose))
    await user.type(await screen.findByLabelText('Simulated user system prompt'), ' uncommitted')
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(techniques.validateEdit).not.toHaveBeenCalled()
    expect(techniques.createTechnique).not.toHaveBeenCalled()
  })

  it.each(['fixed', 'none'])('should preserve the existing %s final-message mode', async (mode: string) => {
    const user = userEvent.setup()
    const original = configuration()
    original.settings.simulation = {
      ...original.settings.simulation, next_message_system_prompt: null,
      final_user_message: mode === 'fixed' ? 'yes.' : null,
    }
    render(tree(original))
    await screen.findByText('Movie role play')
    expect(screen.queryByLabelText('Next-message system prompt')).not.toBeInTheDocument()
    if (mode === 'fixed') {
      const final = screen.getByLabelText('Fixed final user message')
      expect(final).toHaveValue('yes.')
      await user.clear(final)
      await user.type(final, 'continue.')
    } else {
      expect(screen.queryByText('No additional user message.')).not.toBeInTheDocument()
      expect(screen.queryByLabelText('Fixed final user message')).not.toBeInTheDocument()
    }
    await user.click(screen.getByRole('button', { name: 'Apply to run' }))
    await waitFor(() => expect(techniques.validateEdit).toHaveBeenCalled())
    expect(techniques.validateEdit.mock.calls[0][0].changes).toEqual(
      mode === 'fixed' ? { simulation: { final_user_message: 'continue.' } } : {},
    )
  })

  it('should reopen applied settings and remove a reverted edit from the delta', async () => {
    const user = userEvent.setup()
    const original = configuration()
    const initial = { ...original, settings: {
      ...original.settings, simulation: { ...original.settings.simulation, num_turns: 4 },
    } }
    render(tree(original, jest.fn(), initial))
    const turns = await screen.findByRole('spinbutton', { name: 'Turn count' })
    expect(turns).toHaveValue(4)
    await user.clear(turns)
    await user.type(turns, '2')
    await user.click(screen.getByRole('button', { name: 'Apply to run' }))
    await waitFor(() => expect(techniques.validateEdit).toHaveBeenCalled())
    expect(techniques.validateEdit.mock.calls[0][0].changes).toEqual({})
  })

  it('should distinguish an explicit empty text value from an untouched default', async () => {
    const user = userEvent.setup()
    const original = configuration()
    original.parameters.push({ name: 'text_setting', type_name: 'str', required: false })
    original.settings.params = { ...original.settings.params, text_setting: 'source text' }
    render(tree(original))
    await user.clear(await screen.findByLabelText('text_setting'))
    await user.click(screen.getByRole('button', { name: 'Apply to run' }))
    await waitFor(() => expect(techniques.validateEdit).toHaveBeenCalled())
    expect(techniques.validateEdit.mock.calls[0][0].changes).toEqual({ params: { text_setting: '' } })
  })

  it('should auto-apply inline edits and ignore an obsolete validation response', async () => {
    const user = userEvent.setup()
    const onApplied = jest.fn()
    const onPending = jest.fn()
    const original = configuration()
    let resolveFirst: (value: Awaited<ReturnType<typeof techniquesApi.validateEdit>>) => void = () => {
      throw new Error('First validation has not started')
    }
    techniques.validateEdit.mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve }))
    render(<FluentProvider theme={webLightTheme}>
      <TechniqueEditor original={original} autoApply onApplied={onApplied} onPending={onPending} />
    </FluentProvider>)
    const turns = await screen.findByRole('spinbutton', { name: 'Turn count' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Apply to run' })).not.toBeInTheDocument()
    await user.clear(turns)
    await user.type(turns, '3')
    expect(onPending).toHaveBeenCalled()
    await waitFor(() => expect(techniques.validateEdit).toHaveBeenCalledTimes(1))
    await user.clear(turns)
    await user.type(turns, '4')
    await waitFor(() => expect(onApplied).toHaveBeenCalledTimes(1))
    expect(onApplied.mock.calls[0][0].override.changes).toEqual({ simulation: { num_turns: 4 } })
    resolveFirst({
      configuration: original, override: {
        source_name: original.source_name, source_identity: original.source_identity,
        changes: { simulation: { num_turns: 3 } }, dependencies: {}, effective_identity: 'obsolete',
      },
    })
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Technique settings applied'))
    expect(onApplied).toHaveBeenCalledTimes(1)
    expect(turns).toHaveValue(4)
  })

  it('should not apply an inline draft after it unmounts', async () => {
    const user = userEvent.setup()
    const onApplied = jest.fn()
    const original = configuration()
    let resolveEdit: (value: Awaited<ReturnType<typeof techniquesApi.validateEdit>>) => void = () => {
      throw new Error('Validation has not started')
    }
    techniques.validateEdit.mockImplementationOnce(() => new Promise((resolve) => { resolveEdit = resolve }))
    const view = render(<FluentProvider theme={webLightTheme}>
      <TechniqueEditor original={original} autoApply onApplied={onApplied} />
    </FluentProvider>)
    await user.type(await screen.findByLabelText('Simulated user system prompt'), ' changed')
    await waitFor(() => expect(techniques.validateEdit).toHaveBeenCalledTimes(1))
    view.unmount()
    resolveEdit({
      configuration: original, override: {
        source_name: original.source_name, source_identity: original.source_identity,
        changes: { simulation: { adversarial_chat_system_prompt: 'Changed' } }, dependencies: {}, effective_identity: 'obsolete',
      },
    })
    await Promise.resolve()
    expect(onApplied).not.toHaveBeenCalled()
  })
})
