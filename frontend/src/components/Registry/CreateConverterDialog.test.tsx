import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import { convertersApi, targetsApi } from '@/services/api'
import type { Parameter } from '@/types'

import CreateConverterDialog from './CreateConverterDialog'

jest.mock('@/services/api', () => ({
  convertersApi: {
    listConverterTypes: jest.fn(),
    listConverters: jest.fn(),
    createConverter: jest.fn(),
  },
  targetsApi: {
    listTargets: jest.fn(),
  },
}))

const mockedConvertersApi = convertersApi as jest.Mocked<typeof convertersApi>
const mockedTargetsApi = targetsApi as jest.Mocked<typeof targetsApi>

const converterTypes = {
  items: [
    {
      converter_type: 'CaesarConverter',
      supported_input_types: ['text'],
      supported_output_types: ['text'],
      parameters: [
        {
          name: 'caesar_offset',
          type_name: 'int',
          required: true,
          default: null,
          choices: null,
          description: 'Offset for the cipher.',
        },
      ],
      is_llm_based: false,
      description: 'Applies a Caesar cipher.',
    },
  ],
}

const wordSelectionParameter: Parameter = {
  name: 'word_selection_strategy',
  type_name: 'WordSelectionStrategy',
  required: false,
  default: null,
  variants: {
    all: [],
    random: [
      { name: 'proportion', type_name: 'float', required: true, default: null },
      { name: 'seed', type_name: 'int', required: false, default: null },
    ],
    indices: [
      { name: 'indices', type_name: 'list[int]', is_list: true, required: true },
    ],
    keywords: [
      { name: 'keywords', type_name: 'list[str]', is_list: true, required: true, default: null },
      { name: 'case_sensitive', type_name: 'bool', required: false, default: 'True' },
    ],
    content: [
      { name: 'max_words', type_name: 'int', required: false, default: '2' },
      { name: 'skip_first', type_name: 'int', required: false, default: '1' },
      { name: 'min_word_length', type_name: 'int', required: false, default: '3' },
      { name: 'stopwords', type_name: 'list[str]', is_list: true, required: false, default: null },
      { name: 'candidate_words', type_name: 'list[str]', is_list: true, required: false, default: null },
    ],
  },
}

function mockConverterParameters(parameters: Parameter[], converterType = 'TextConverter') {
  mockedConvertersApi.listConverterTypes.mockResolvedValue({
    items: [{ ...converterTypes.items[0], converter_type: converterType, parameters }],
  })
}

async function selectConverterType(converterType: string) {
  const user = userEvent.setup()
  await user.click(await screen.findByRole('combobox', { name: /^converter type$/i }))
  await user.click(screen.getByTestId(`converter-type-option-${converterType}`))
}

function dialogTree(
  props: Partial<React.ComponentProps<typeof CreateConverterDialog>> = {},
) {
  return (
    <FluentProvider theme={webLightTheme}>
      <CreateConverterDialog
        open
        onClose={jest.fn()}
        onCreated={jest.fn()}
        {...props}
      />
    </FluentProvider>
  )
}

function renderDialog(
  props: Partial<React.ComponentProps<typeof CreateConverterDialog>> = {},
) {
  return render(dialogTree(props))
}

async function submitCaesarConverter(user: ReturnType<typeof userEvent.setup>) {
  await selectConverterType('CaesarConverter')
  await user.type(screen.getByLabelText(/caesar_offset/i), '5')
  await user.click(screen.getByRole('button', { name: 'Add Converter' }))
}

describe('CreateConverterDialog', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockedConvertersApi.listConverterTypes.mockResolvedValue(converterTypes)
    mockedConvertersApi.listConverters.mockResolvedValue({ items: [] })
    mockedTargetsApi.listTargets.mockResolvedValue({
      items: [],
      pagination: { limit: 200, has_more: false },
    })
    mockedConvertersApi.createConverter.mockResolvedValue({
      converter_id: 'created',
      identifier: {
        class_name: 'TextConverter',
        class_module: 'pyrit.converter',
        hash: 'converter-hash',
        pyrit_version: '0.0.0',
      },
    })
  })

  afterEach(() => {
    jest.restoreAllMocks()
  })

  it('loads converter classes from registry type metadata', async () => {
    const user = userEvent.setup()
    renderDialog()

    await user.click(await screen.findByRole('combobox', { name: /^converter type$/i }))
    expect(screen.getByTestId('converter-type-option-CaesarConverter')).toHaveTextContent(
      'Applies a Caesar cipher.',
    )
    expect(screen.getByRole('group', { name: 'Text to Text' })).toBeInTheDocument()
    expect(mockedConvertersApi.listConverterTypes).toHaveBeenCalledTimes(1)
  })

  it('prefills an editable registry name from the selected type', async () => {
    const user = userEvent.setup()
    renderDialog()
    await selectConverterType('CaesarConverter')

    const nameInput = screen.getByLabelText(/registry name/i)
    expect(nameInput).toHaveValue('CaesarConverter')
    await user.clear(nameInput)
    await user.type(nameInput, 'caesar-custom')
    expect(nameInput).toHaveValue('caesar-custom')
  })

  it('requires constructor parameters before creating', async () => {
    const user = userEvent.setup()
    renderDialog()
    await selectConverterType('CaesarConverter')
    await user.click(screen.getByRole('button', { name: 'Add Converter' }))

    expect(screen.getByText('Required')).toBeInTheDocument()
    expect(mockedConvertersApi.createConverter).not.toHaveBeenCalled()
  })

  it('offers upload or server path input for every Path parameter', async () => {
    mockedConvertersApi.listConverterTypes.mockResolvedValue({
      items: [{
        converter_type: 'PathConverter',
        supported_input_types: ['text'],
        supported_output_types: ['text'],
        parameters: [{
          name: 'source',
          type_name: 'Path',
          required: true,
          default: null,
          choices: null,
          description: 'Input asset.',
        }],
        is_llm_based: false,
        description: 'Uses an input asset.',
      }],
    })
    renderDialog()

    await selectConverterType('PathConverter')

    expect(screen.getByLabelText('source *')).toHaveAttribute(
      'placeholder',
      'Upload a file or enter a server path',
    )
    expect(screen.getByRole('button', { name: 'Upload' })).toBeInTheDocument()
  })

  it('creates a named converter through the registry API', async () => {
    const onCreated = jest.fn()
    mockedConvertersApi.createConverter.mockResolvedValue({
      converter_id: 'caesar-custom',
      identifier: {
        class_name: 'CaesarConverter',
        class_module: 'pyrit.converter',
        hash: 'caesar-hash',
        pyrit_version: '0.0.0',
      },
    })
    const user = userEvent.setup()
    renderDialog({ onCreated })
    await selectConverterType('CaesarConverter')
    const nameInput = screen.getByLabelText(/registry name/i)
    await user.clear(nameInput)
    await user.type(nameInput, 'caesar-custom')
    await user.type(screen.getByLabelText(/caesar_offset/i), '5')
    await user.click(screen.getByRole('button', { name: 'Add Converter' }))

    expect(mockedConvertersApi.createConverter).toHaveBeenCalledWith({
      name: 'caesar-custom',
      type: 'CaesarConverter',
      params: { caesar_offset: '5' },
    })
    expect(onCreated).toHaveBeenCalledWith('caesar-custom')
  })

  it.each([
    ['ImageOverlayConverter', 'base_image'],
    ['AddImageVideoConverter', 'video_path'],
  ])('should accept a URL or upload for %s.%s', async (converterType, parameterName) => {
    const user = userEvent.setup()
    mockConverterParameters([{
      name: parameterName, type_name: 'Path | str', required: true, default: null,
    }], converterType)
    renderDialog()
    await selectConverterType(converterType)
    const input = screen.getByRole('textbox', { name: `${parameterName} *` })
    expect(input).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Upload' })).toBeEnabled()
    const url = 'https://example.blob.core.windows.net/assets/input'
    await user.type(input, url)
    await user.click(screen.getByRole('button', { name: 'Add Converter' }))
    expect(mockedConvertersApi.createConverter).toHaveBeenCalledWith({
      name: converterType, type: converterType, params: { [parameterName]: url },
    })
  })

  it.each(['str | list[str]', 'list[str] | str', 'dict[str, list[int | float]] | str'])(
    'should create SearchReplaceConverter with a string replacement for %s',
    async (typeName) => {
      const user = userEvent.setup()
      mockConverterParameters([
        { name: 'pattern', type_name: 'str', required: true },
        { name: 'replace', type_name: typeName, required: true },
        { name: 'regex_flags', type_name: 'int', required: false, default: '0' },
      ], 'SearchReplaceConverter')
      renderDialog()
      await selectConverterType('SearchReplaceConverter')

      const replacement = screen.getByRole('textbox', { name: 'replace *' })
      expect(replacement).toBeEnabled()
      await user.type(screen.getByRole('textbox', { name: 'pattern *' }), 'hello')
      await user.click(screen.getByRole('button', { name: 'Add Converter' }))
      expect(mockedConvertersApi.createConverter).not.toHaveBeenCalled()
      expect(screen.getByText('Required')).toBeInTheDocument()

      await user.type(replacement, 'world')
      await user.click(screen.getByRole('button', { name: 'Add Converter' }))
      expect(mockedConvertersApi.createConverter).toHaveBeenCalledWith({
        name: 'SearchReplaceConverter',
        type: 'SearchReplaceConverter',
        params: { pattern: 'hello', replace: 'world', regex_flags: '0' },
      })
    },
  )

  it('selects a registered target for a target reference parameter', async () => {
    mockedConvertersApi.listConverterTypes.mockResolvedValue({
      items: [
        {
          converter_type: 'PersuasionConverter',
          supported_input_types: ['text'],
          supported_output_types: ['text'],
          parameters: [
            {
              name: 'converter_target',
              type_name: 'PromptTarget',
              required: true,
              default: null,
              choices: null,
              reference_type: 'target',
              description: 'The target used to rewrite prompts.',
            },
          ],
          is_llm_based: true,
          description: 'Rewrites prompts.',
        },
      ],
    })
    mockedTargetsApi.listTargets.mockResolvedValue({
      items: [
        {
          target_registry_name: 'rewrite-target',
          identifier: {
            class_name: 'OpenAIChatTarget',
            class_module: 'pyrit.prompt_target',
            hash: 'target-hash',
            pyrit_version: '0.0.0',
          },
        },
      ],
      pagination: { limit: 200, has_more: false },
    })
    mockedConvertersApi.createConverter.mockResolvedValue({
      converter_id: 'persuasion',
      identifier: {
        class_name: 'PersuasionConverter',
        class_module: 'pyrit.converter',
        hash: 'persuasion-hash',
        pyrit_version: '0.0.0',
      },
    })
    const user = userEvent.setup()
    renderDialog()

    await selectConverterType('PersuasionConverter')
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getAllByText('Rewrites prompts.')).not.toHaveLength(0)
    expect(screen.getAllByText('LLM')).not.toHaveLength(0)
    await user.selectOptions(
      screen.getByRole('combobox', { name: /converter_target/i }),
      'rewrite-target',
    )
    await user.click(screen.getByRole('button', { name: 'Add Converter' }))

    expect(mockedConvertersApi.createConverter).toHaveBeenCalledWith({
      name: 'PersuasionConverter',
      type: 'PersuasionConverter',
      params: { converter_target: 'rewrite-target' },
    })
  })

  it('shows duplicate-name errors from the registry', async () => {
    mockedConvertersApi.createConverter.mockRejectedValue(
      new Error("Converter instance 'CaesarConverter' already exists"),
    )
    const user = userEvent.setup()
    renderDialog()
    await selectConverterType('CaesarConverter')
    await user.type(screen.getByLabelText(/caesar_offset/i), '5')
    await user.click(screen.getByRole('button', { name: 'Add Converter' }))

    expect(
      await screen.findByText(/already exists/i),
    ).toBeInTheDocument()
  })

  it('should not move focus onto an error from loading converter types', async () => {
    mockedConvertersApi.listConverterTypes.mockRejectedValue(
      new Error('converter registry unavailable'),
    )
    renderDialog()

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/converter registry unavailable/i)
    await act(async () => {
      await new Promise<void>((resolve) => { requestAnimationFrame(() => resolve()) })
    })

    expect(alert).not.toHaveFocus()
  })

  it('should keep keyboard focus inside the dialog when creation fails', async () => {
    const onClose = jest.fn()
    mockedConvertersApi.createConverter.mockRejectedValue(
      new Error("Converter instance 'CaesarConverter' already exists"),
    )
    const user = userEvent.setup()
    renderDialog({ onClose })
    await selectConverterType('CaesarConverter')
    await user.type(screen.getByLabelText(/caesar_offset/i), '5')
    await user.click(screen.getByRole('button', { name: 'Add Converter' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/already exists/i)
    await waitFor(() => expect(alert).toHaveFocus())
    expect(alert.closest('[role="dialog"]')).toBe(screen.getByRole('dialog'))

    await user.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalled()
  })

  it('should focus a submission error even when a frame runs before React renders it', async () => {
    // A frame callback can run before React commits the message bar, which is
    // when focusing from one finds no node and silently does nothing.
    jest.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
      callback(performance.now())
      return 0
    })
    mockedConvertersApi.createConverter.mockRejectedValue(
      new Error("Converter instance 'CaesarConverter' already exists"),
    )
    const user = userEvent.setup()
    renderDialog()
    await submitCaesarConverter(user)

    const alert = await screen.findByRole('alert')
    await waitFor(() => expect(alert).toHaveFocus())
  })

  it('should not clear a later opening of the dialog when an earlier creation succeeds', async () => {
    let resolveCreate: ((value: { converter_id: string }) => void) | undefined
    mockedConvertersApi.createConverter.mockImplementation(
      () => new Promise((resolve) => { resolveCreate = resolve }),
    )
    const onCreated = jest.fn()
    const user = userEvent.setup()
    const { rerender } = renderDialog({ onCreated })
    await submitCaesarConverter(user)
    await waitFor(() => expect(mockedConvertersApi.createConverter).toHaveBeenCalled())

    // Dismissed while the request was in flight, then opened again and filled in.
    rerender(dialogTree({ open: false, onCreated }))
    rerender(dialogTree({ open: true, onCreated }))
    await screen.findByRole('combobox', { name: /^converter type$/i })
    await selectConverterType('CaesarConverter')
    // Selecting a type prefills the registry name, so replace it rather than append.
    await user.clear(screen.getByLabelText(/registry name/i))
    await user.type(screen.getByLabelText(/registry name/i), 'my-second-converter')

    await act(async () => {
      resolveCreate?.({ converter_id: 'first-converter' })
    })

    // The list still refreshes, but the opening in front of the user is untouched.
    expect(onCreated).toHaveBeenCalledWith('first-converter')
    expect(screen.getByLabelText(/registry name/i)).toHaveValue('my-second-converter')
    expect(screen.getByLabelText(/caesar_offset/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add Converter' })).toBeInTheDocument()
  })

  it('should not surface a submission error in a later opening of the dialog', async () => {
    let rejectCreate: ((reason: Error) => void) | undefined
    mockedConvertersApi.createConverter.mockImplementation(
      () => new Promise((_, reject) => { rejectCreate = reject }),
    )
    const onClose = jest.fn()
    const user = userEvent.setup()
    const { rerender } = renderDialog({ onClose })
    await submitCaesarConverter(user)
    await waitFor(() => expect(mockedConvertersApi.createConverter).toHaveBeenCalled())

    // Dismissed while the request was in flight, then opened again.
    await user.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalled()
    rerender(dialogTree({ open: false, onClose }))
    rerender(dialogTree({ open: true, onClose }))
    await screen.findByRole('combobox', { name: /^converter type$/i })

    await act(async () => {
      rejectCreate?.(new Error("Converter instance 'CaesarConverter' already exists"))
    })

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByLabelText(/registry name/i)).toHaveValue('')
  })

  it('should display and submit the Binary enum default from metadata', async () => {
    const user = userEvent.setup()
    mockConverterParameters([{
      name: 'bits_per_char',
      type_name: 'BitsPerChar',
      required: false,
      choices: ['8', '16', '32'],
      default: '16',
    }], 'BinaryConverter')
    renderDialog()
    await selectConverterType('BinaryConverter')

    expect(screen.getByRole('combobox', { name: 'bits_per_char' })).toHaveValue('16')
    await user.click(screen.getByRole('button', { name: 'Add Converter' }))
    expect(mockedConvertersApi.createConverter).toHaveBeenCalledWith({
      name: 'BinaryConverter',
      type: 'BinaryConverter',
      params: { bits_per_char: '16' },
    })
  })

  it('should submit random strategy fields as numbers', async () => {
    const user = userEvent.setup()
    mockConverterParameters([wordSelectionParameter])
    renderDialog()
    await selectConverterType('TextConverter')
    await user.selectOptions(screen.getByRole('combobox', { name: 'word_selection_strategy' }), 'random')

    const proportion = screen.getByRole('spinbutton', { name: 'proportion *' })
    expect(proportion).toHaveValue(null)
    await user.click(screen.getByRole('button', { name: 'Add Converter' }))
    expect(screen.getByText('proportion is required.')).toBeInTheDocument()
    expect(mockedConvertersApi.createConverter).not.toHaveBeenCalled()
    await user.type(proportion, '0.3')
    await user.type(screen.getByRole('spinbutton', { name: 'seed' }), '42')
    await user.click(screen.getByRole('button', { name: 'Add Converter' }))

    expect(mockedConvertersApi.createConverter).toHaveBeenCalledWith({
      name: 'TextConverter',
      type: 'TextConverter',
      params: { word_selection_strategy: { type: 'random', parameters: { proportion: 0.3, seed: 42 } } },
    })
  })

  it('should validate required indices and submit a numeric list', async () => {
    const user = userEvent.setup()
    mockConverterParameters([wordSelectionParameter])
    renderDialog()
    await selectConverterType('TextConverter')
    await user.selectOptions(screen.getByRole('combobox', { name: 'word_selection_strategy' }), 'indices')
    await user.click(screen.getByRole('button', { name: 'Add Converter' }))
    expect(screen.getByText('indices is required.')).toBeInTheDocument()
    expect(mockedConvertersApi.createConverter).not.toHaveBeenCalled()

    await user.type(screen.getByRole('textbox', { name: 'indices *' }), '0, 2, 5')
    await user.click(screen.getByRole('button', { name: 'Add Converter' }))
    expect(mockedConvertersApi.createConverter).toHaveBeenCalledWith({
      name: 'TextConverter',
      type: 'TextConverter',
      params: { word_selection_strategy: { type: 'indices', parameters: { indices: [0, 2, 5] } } },
    })
  })

  it('should drop previous strategy fields and submit keyword arrays and booleans', async () => {
    const user = userEvent.setup()
    mockConverterParameters([wordSelectionParameter])
    renderDialog()
    await selectConverterType('TextConverter')
    const strategy = screen.getByRole('combobox', { name: 'word_selection_strategy' })
    await user.selectOptions(strategy, 'random')
    await user.type(screen.getByRole('spinbutton', { name: 'seed' }), '42')
    await user.selectOptions(strategy, 'keywords')

    expect(screen.queryByRole('spinbutton', { name: 'seed' })).not.toBeInTheDocument()
    const keywords = screen.getByRole('textbox', { name: 'keywords *' })
    expect(keywords).toHaveValue('')
    await user.type(keywords, 'hello, world')
    await user.selectOptions(screen.getByRole('combobox', { name: 'case_sensitive' }), 'false')
    await user.click(screen.getByRole('button', { name: 'Add Converter' }))
    expect(mockedConvertersApi.createConverter).toHaveBeenCalledWith({
      name: 'TextConverter',
      type: 'TextConverter',
      params: {
        word_selection_strategy: {
          type: 'keywords',
          parameters: { keywords: ['hello', 'world'], case_sensitive: false },
        },
      },
    })
  })

  it.each([
    ['indices', '0, nope', 'indices must be a number.'],
    ['indices', '0, 1.5', 'indices must be an integer.'],
    ['indices', '0, Infinity', 'indices must be a number.'],
    ['random', '1.5', 'seed must be an integer.'],
  ])('should reject invalid %s input %s without submitting', async (strategy, value, message) => {
    const user = userEvent.setup()
    mockConverterParameters([wordSelectionParameter])
    renderDialog()
    await selectConverterType('TextConverter')
    await user.selectOptions(screen.getByRole('combobox', { name: 'word_selection_strategy' }), strategy)
    if (strategy === 'random') {
      await user.type(screen.getByRole('spinbutton', { name: 'proportion *' }), '0.3')
    }
    const input = strategy === 'indices'
      ? screen.getByRole('textbox', { name: 'indices *' })
      : screen.getByRole('spinbutton', { name: 'seed' })
    await user.type(input, value)
    await user.click(screen.getByRole('button', { name: 'Add Converter' }))

    expect(screen.getByText(message)).toBeInTheDocument()
    expect(mockedConvertersApi.createConverter).not.toHaveBeenCalled()
  })

  it.each([false, true])('should omit the strategy to preserve the converter default (reset: %s)', async (reset) => {
    const user = userEvent.setup()
    mockConverterParameters([wordSelectionParameter])
    renderDialog()
    await selectConverterType('TextConverter')
    const strategy = screen.getByRole('combobox', { name: 'word_selection_strategy' })
    expect(strategy).toHaveValue('')
    if (reset) {
      await user.selectOptions(strategy, 'random')
      await user.selectOptions(strategy, '')
    }
    await user.click(screen.getByRole('button', { name: 'Add Converter' }))
    expect(mockedConvertersApi.createConverter).toHaveBeenCalledWith({
      name: 'TextConverter',
      type: 'TextConverter',
      params: {},
    })
  })

  it.each(['stopwords', 'candidate_words'])('should preserve an explicit empty %s array', async (name) => {
    const user = userEvent.setup()
    mockConverterParameters([wordSelectionParameter])
    renderDialog()
    await selectConverterType('TextConverter')
    await user.selectOptions(screen.getByRole('combobox', { name: 'word_selection_strategy' }), 'content')
    await user.click(screen.getByRole('checkbox', { name: `Use empty list for ${name}` }))
    expect(screen.getByRole('textbox', { name })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Add Converter' }))
    expect(mockedConvertersApi.createConverter).toHaveBeenCalledWith({
      name: 'TextConverter',
      type: 'TextConverter',
      params: {
        word_selection_strategy: {
          type: 'content',
          parameters: { max_words: 2, skip_first: 1, min_word_length: 3, [name]: [] },
        },
      },
    })
  })

  it('should restore omission when the empty-list choice is cleared', async () => {
    const user = userEvent.setup()
    mockConverterParameters([wordSelectionParameter])
    renderDialog()
    await selectConverterType('TextConverter')
    await user.selectOptions(screen.getByRole('combobox', { name: 'word_selection_strategy' }), 'content')
    const emptyList = screen.getByRole('checkbox', { name: 'Use empty list for stopwords' })
    await user.click(emptyList)
    await user.click(emptyList)
    expect(screen.getByRole('textbox', { name: 'stopwords' })).toBeEnabled()
    await user.click(screen.getByRole('button', { name: 'Add Converter' }))
    expect(mockedConvertersApi.createConverter).toHaveBeenCalledWith({
      name: 'TextConverter',
      type: 'TextConverter',
      params: {
        word_selection_strategy: {
          type: 'content', parameters: { max_words: 2, skip_first: 1, min_word_length: 3 },
        },
      },
    })
  })

  it.each([
    'Optional[UnknownStrategy]',
    'UnknownStrategy',
    'UnknownStrategy | OtherStrategy',
    'dict[str, list[int | str | float]]',
    'list[str] | UnknownStrategy',
    'int | float',
  ])('should not expose unsupported %s inputs as editable strings', async (typeName) => {
    const user = userEvent.setup()
    mockConverterParameters([{
      name: 'unresolved',
      type_name: typeName,
      required: false,
      default: 'UnknownStrategy()',
    }])
    renderDialog()
    await selectConverterType('TextConverter')
    expect(screen.getByRole('textbox', { name: 'unresolved' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Add Converter' }))
    expect(mockedConvertersApi.createConverter).toHaveBeenCalledWith({
      name: 'TextConverter',
      type: 'TextConverter',
      params: {},
    })
  })
})
