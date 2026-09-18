import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import { convertersApi, targetsApi } from '@/services/api'

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

async function selectConverterType(converterType: string) {
  const user = userEvent.setup()
  await user.click(await screen.findByRole('combobox', { name: /^converter type$/i }))
  await user.click(screen.getByTestId(`converter-type-option-${converterType}`))
}

function renderDialog(
  props: Partial<React.ComponentProps<typeof CreateConverterDialog>> = {},
) {
  return render(
    <FluentProvider theme={webLightTheme}>
      <CreateConverterDialog
        open
        onClose={jest.fn()}
        onCreated={jest.fn()}
        {...props}
      />
    </FluentProvider>,
  )
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
})
