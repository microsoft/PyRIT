import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { useState } from 'react'

import type { AdditionalInitializerSetting, RegisteredInitializer } from '@/types'

import AdditionalInitializers from './AdditionalInitializers'

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

const targetInitializer: RegisteredInitializer = {
  initializer_name: 'target',
  initializer_type: 'TargetInitializer',
  description: 'Registers targets.',
  required_env_vars: ['AZURE_OPENAI_ENDPOINT'],
  supported_parameters: [
    {
      name: 'tags',
      type_name: 'list[str]',
      required: false,
      default: null,
      choices: null,
      is_list: true,
      description: 'Target tags.',
    },
  ],
}

const scorerInitializer: RegisteredInitializer = {
  initializer_name: 'scorer',
  initializer_type: 'ScorerInitializer',
  description: 'Registers scorers.',
  required_env_vars: [],
  supported_parameters: [
    {
      name: 'mode',
      type_name: 'str',
      required: false,
      default: null,
      choices: null,
      is_list: false,
      description: 'Scorer mode.',
    },
  ],
}

const noParamInitializer: RegisteredInitializer = {
  initializer_name: 'load_default_datasets',
  initializer_type: 'DatasetInitializer',
  description: 'Loads default datasets.',
  required_env_vars: [],
  supported_parameters: [],
}

const taggedTargetInitializer: RegisteredInitializer = {
  initializer_name: 'tagged_target',
  initializer_type: 'TargetInitializer',
  description: 'Registers targets with tags.',
  required_env_vars: [],
  supported_parameters: [
    {
      name: 'tags',
      type_name: 'list[str]',
      required: false,
      default: null,
      choices: ['default', 'scorer', 'all'],
      is_list: true,
      description: 'Target tags.',
    },
  ],
}

const requiredParamInitializer: RegisteredInitializer = {
  initializer_name: 'required_param',
  initializer_type: 'DatasetInitializer',
  description: 'Requires a label.',
  required_env_vars: [],
  supported_parameters: [
    {
      name: 'label',
      type_name: 'str',
      required: true,
      default: null,
      choices: null,
      is_list: false,
      description: 'A required label.',
    },
  ],
}

const refreshInitializer: RegisteredInitializer = {
  initializer_name: 'refresh_datasets',
  initializer_type: 'DatasetInitializer',
  description: 'Refreshes datasets.',
  required_env_vars: [],
  supported_parameters: [
    {
      name: 'days',
      type_name: 'int',
      required: false,
      default: null,
      choices: null,
      is_list: false,
      description: 'Number of days.',
    },
    {
      name: 'dataset_names',
      type_name: 'list[str]',
      required: false,
      default: null,
      choices: null,
      is_list: true,
      description: 'Dataset names.',
    },
  ],
}

const sampleItems: AdditionalInitializerSetting[] = [
  {
    id: 'additional-1',
    initializer_name: 'target',
    parameters: { tags: ['default'] },
    order_index: 2,
  },
  {
    id: 'additional-2',
    initializer_name: 'scorer',
    parameters: null,
    order_index: null,
  },
]

describe('AdditionalInitializers', () => {
  const defaultProps = {
    items: sampleItems,
    registeredInitializers: [targetInitializer, scorerInitializer],
    creating: false,
    onAdd: jest.fn().mockResolvedValue(true),
    onSave: jest.fn().mockResolvedValue(true),
    onClearSaveError: jest.fn(),
    onApply: jest.fn().mockResolvedValue(undefined),
    onRemove: jest.fn().mockResolvedValue(undefined),
  }

  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('should render additional initializer rows and metadata', () => {
    render(
      <TestWrapper>
        <AdditionalInitializers {...defaultProps} />
      </TestWrapper>,
    )

    expect(screen.getByRole('list', { name: 'Additional initializers' })).toBeInTheDocument()
    expect(screen.getByTestId('initializer-row-additional-1')).toHaveTextContent('target')
    expect(screen.getByText('Required env vars: AZURE_OPENAI_ENDPOINT')).toBeInTheDocument()
    expect(screen.getByText('tags (list[str], optional)')).toBeInTheDocument()
  })

  it('should show the saved parameters read-only without an inline editor', () => {
    render(
      <TestWrapper>
        <AdditionalInitializers {...defaultProps} />
      </TestWrapper>,
    )

    const row = screen.getByTestId('initializer-row-additional-1')
    expect(within(row).getByText(/"tags"/)).toBeInTheDocument()
    expect(within(row).queryByRole('textbox', { name: 'Parameters JSON' })).not.toBeInTheDocument()
  })

  it('should show the description as hover text on the initializer name', async () => {
    const user = userEvent.setup()

    render(
      <TestWrapper>
        <AdditionalInitializers {...defaultProps} />
      </TestWrapper>,
    )

    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()

    await user.hover(within(screen.getByTestId('initializer-row-additional-1')).getByText('target'))

    expect(await screen.findByRole('tooltip')).toHaveTextContent('Registers targets.')
  })

  it('should call onSave from the edit dialog, preserving the existing order_index', async () => {
    const user = userEvent.setup()

    render(
      <TestWrapper>
        <AdditionalInitializers {...defaultProps} />
      </TestWrapper>,
    )

    const row = screen.getByTestId('initializer-row-additional-1')
    fireEvent.click(within(row).getByRole('button', { name: 'Edit' }))

    const dialog = await screen.findByRole('dialog', {}, { timeout: 3000 })
    await within(dialog).findByText('Edit target initializer')
    const editor = within(dialog).getByTestId('param-tags')
    fireEvent.change(editor, { target: { value: 'extra' } })
    await user.click(await within(dialog).findByRole('button', { name: 'Save', hidden: true }))

    expect(defaultProps.onSave).toHaveBeenCalledWith('additional-1', {
      parameters: { tags: ['extra'] },
      order_index: 2,
    })
  })

  it('should call onApply with the saved parameters', async () => {
    const user = userEvent.setup()

    render(
      <TestWrapper>
        <AdditionalInitializers {...defaultProps} />
      </TestWrapper>,
    )

    const row = screen.getByTestId('initializer-row-additional-1')
    await user.click(within(row).getByRole('button', { name: 'Apply now' }))

    expect(defaultProps.onApply).toHaveBeenCalledWith('additional-1', 'target', { tags: ['default'] })
  })

  it('should call onRemove with the additional initializer id after confirming', async () => {
    const user = userEvent.setup()

    render(
      <TestWrapper>
        <AdditionalInitializers {...defaultProps} />
      </TestWrapper>,
    )

    await user.click(within(screen.getByTestId('initializer-row-additional-1')).getByRole('button', { name: 'Remove' }))

    const dialog = await screen.findByRole('dialog', { hidden: true })
    expect(within(dialog).getByText(/remove the/i)).toBeInTheDocument()
    expect(within(dialog).getByText('target')).toBeInTheDocument()

    await user.click(within(dialog).getByRole('button', { name: 'Remove', hidden: true }))

    expect(defaultProps.onRemove).toHaveBeenCalledWith('additional-1')
  })

  it('should not call onRemove when the confirmation dialog is cancelled', async () => {
    const user = userEvent.setup()

    render(
      <TestWrapper>
        <AdditionalInitializers {...defaultProps} />
      </TestWrapper>,
    )

    await user.click(within(screen.getByTestId('initializer-row-additional-1')).getByRole('button', { name: 'Remove' }))

    const dialog = await screen.findByRole('dialog', { hidden: true })
    await user.click(within(dialog).getByRole('button', { name: 'Cancel', hidden: true }))

    expect(defaultProps.onRemove).not.toHaveBeenCalled()
  })

  it('should show a validation error when a required parameter is missing', async () => {
    const user = userEvent.setup()

    render(
      <TestWrapper>
        <AdditionalInitializers {...defaultProps} registeredInitializers={[requiredParamInitializer]} />
      </TestWrapper>,
    )

    fireEvent.change(screen.getByRole('combobox', { name: 'Initializer to add' }), {
      target: { value: 'required_param' },
    })
    await user.click(screen.getByRole('button', { name: 'Add initializer' }))

    const dialog = await screen.findByRole('dialog', {}, { timeout: 3000 })
    await within(dialog).findByText('Add required_param initializer')
    await user.click(await within(dialog).findByRole('button', { name: 'Add', hidden: true }))

    expect(await within(dialog).findByRole('alert', { hidden: true })).toHaveTextContent(
      'label is required.',
    )
    expect(defaultProps.onAdd).not.toHaveBeenCalled()
  })

  it('should submit typed number and list parameters from the add dialog', async () => {
    const user = userEvent.setup()

    render(
      <TestWrapper>
        <AdditionalInitializers {...defaultProps} registeredInitializers={[refreshInitializer]} />
      </TestWrapper>,
    )

    fireEvent.change(screen.getByRole('combobox', { name: 'Initializer to add' }), {
      target: { value: 'refresh_datasets' },
    })
    await user.click(screen.getByRole('button', { name: 'Add initializer' }))

    const dialog = await screen.findByRole('dialog', {}, { timeout: 3000 })
    await within(dialog).findByText('Add refresh_datasets initializer')
    fireEvent.change(within(dialog).getByTestId('param-days'), { target: { value: '7' } })
    fireEvent.change(within(dialog).getByTestId('param-dataset_names'), { target: { value: 'harmbench, xstest' } })
    await user.click(await within(dialog).findByRole('button', { name: 'Add', hidden: true }))

    expect(defaultProps.onAdd).toHaveBeenCalledWith('refresh_datasets', {
      days: 7,
      dataset_names: ['harmbench', 'xstest'],
    })
  })

  it('should submit selected choices from a multiselect parameter', async () => {
    const user = userEvent.setup()

    render(
      <TestWrapper>
        <AdditionalInitializers {...defaultProps} registeredInitializers={[taggedTargetInitializer]} />
      </TestWrapper>,
    )

    fireEvent.change(screen.getByRole('combobox', { name: 'Initializer to add' }), {
      target: { value: 'tagged_target' },
    })
    await user.click(screen.getByRole('button', { name: 'Add initializer' }))

    const dialog = await screen.findByRole('dialog', {}, { timeout: 3000 })
    await within(dialog).findByText('Add tagged_target initializer')
    await user.click(within(dialog).getByTestId('param-tags-default'))
    await user.click(within(dialog).getByTestId('param-tags-scorer'))
    await user.click(await within(dialog).findByRole('button', { name: 'Add', hidden: true }))

    expect(defaultProps.onAdd).toHaveBeenCalledWith('tagged_target', { tags: ['default', 'scorer'] })
  })

  it('should keep the edit dialog open and show an inline error when save fails', async () => {
    const user = userEvent.setup()
    const onSave = jest.fn()
    const onClearSaveError = jest.fn()

    function TestComponent() {
      const [saveErrors, setSaveErrors] = useState<Record<string, string>>({})

      return (
        <AdditionalInitializers
          {...defaultProps}
          saveErrors={saveErrors}
          onSave={async (id, request) => {
            onSave(id, request)
            setSaveErrors({ [id]: 'Mock save failure' })
            return false
          }}
          onClearSaveError={(id) => {
            onClearSaveError(id)
            setSaveErrors({})
          }}
        />
      )
    }

    render(
      <TestWrapper>
        <TestComponent />
      </TestWrapper>,
    )

    const row = screen.getByTestId('initializer-row-additional-1')
    fireEvent.click(within(row).getByRole('button', { name: 'Edit' }))

    const dialog = await screen.findByRole('dialog', {}, { timeout: 3000 })
    await within(dialog).findByText('Edit target initializer')
    const editor = within(dialog).getByTestId('param-tags')
    fireEvent.change(editor, { target: { value: 'modified' } })
    await user.click(await within(dialog).findByRole('button', { name: 'Save', hidden: true }))

    expect(screen.getByRole('dialog', { hidden: true })).toBeInTheDocument()
    expect(await within(dialog).findByRole('alert', { hidden: true })).toHaveTextContent('Mock save failure')
    expect(editor).toHaveValue('modified')

    await user.click(within(dialog).getByRole('button', { name: 'Cancel', hidden: true }))

    expect(onClearSaveError).toHaveBeenCalledWith('additional-1')
  })

  it('should hide the parameters editor and submit null for a no-parameter initializer', async () => {
    const user = userEvent.setup()

    render(
      <TestWrapper>
        <AdditionalInitializers
          {...defaultProps}
          registeredInitializers={[targetInitializer, scorerInitializer, noParamInitializer]}
        />
      </TestWrapper>,
    )

    fireEvent.change(screen.getByRole('combobox', { name: 'Initializer to add' }), {
      target: { value: 'load_default_datasets' },
    })
    await user.click(screen.getByRole('button', { name: 'Add initializer' }))

    const dialog = await screen.findByRole('dialog', {}, { timeout: 3000 })
    await within(dialog).findByText('Add load_default_datasets initializer')
    expect(within(dialog).getByText('This initializer takes no parameters.')).toBeInTheDocument()
    expect(
      within(dialog).queryByRole('textbox', { name: 'Parameters JSON', hidden: true }),
    ).not.toBeInTheDocument()

    await user.click(await within(dialog).findByRole('button', { name: 'Add', hidden: true }))

    expect(defaultProps.onAdd).toHaveBeenCalledWith('load_default_datasets', null)
  })

  it('should show a server error inside the add dialog when onAdd fails', async () => {
    const user = userEvent.setup()

    const props = {
      ...defaultProps,
      registeredInitializers: [refreshInitializer],
      onAdd: jest.fn().mockRejectedValue(new Error('Invalid days value.')),
    }

    render(
      <TestWrapper>
        <AdditionalInitializers {...props} />
      </TestWrapper>,
    )

    fireEvent.change(screen.getByRole('combobox', { name: 'Initializer to add' }), {
      target: { value: 'refresh_datasets' },
    })
    await user.click(screen.getByRole('button', { name: 'Add initializer' }))

    const dialog = await screen.findByRole('dialog', {}, { timeout: 3000 })
    await within(dialog).findByText('Add refresh_datasets initializer')
    fireEvent.change(within(dialog).getByTestId('param-days'), { target: { value: '12' } })
    await user.click(await within(dialog).findByRole('button', { name: 'Add', hidden: true }))

    expect(await within(dialog).findByRole('alert', { hidden: true })).toHaveTextContent(
      'Invalid days value.',
    )
    expect(dialog).toBeInTheDocument()
  })
})
