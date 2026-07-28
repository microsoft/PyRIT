import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import type { AdditionalInitializerSetting, RegisteredInitializer } from '@/types'

import InitializerList from './InitializerList'

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

const sampleItems: AdditionalInitializerSetting[] = [
  {
    id: 'additional-1',
    initializer: targetInitializer,
    parameters: { tags: ['default'] },
    order_index: 2,
  },
  {
    id: 'additional-2',
    initializer: scorerInitializer,
    parameters: null,
    order_index: null,
  },
]

describe('InitializerList', () => {
  const defaultProps = {
    items: sampleItems,
    onSave: jest.fn().mockResolvedValue(undefined),
    onApply: jest.fn().mockResolvedValue(undefined),
    onRemove: jest.fn().mockResolvedValue(undefined),
  }

  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('should render additional initializer rows and metadata', () => {
    render(
      <TestWrapper>
        <InitializerList {...defaultProps} />
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
        <InitializerList {...defaultProps} />
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
        <InitializerList {...defaultProps} />
      </TestWrapper>,
    )

    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()

    await user.hover(screen.getByText('target'))

    expect(await screen.findByRole('tooltip')).toHaveTextContent('Registers targets.')
  })

  it('should call onSave from the edit dialog, preserving the existing order_index', async () => {
    const user = userEvent.setup()

    render(
      <TestWrapper>
        <InitializerList {...defaultProps} />
      </TestWrapper>,
    )

    const row = screen.getByTestId('initializer-row-additional-1')
    await user.click(within(row).getByRole('button', { name: 'Edit' }))

    const dialog = await screen.findByRole('dialog', { name: 'Edit target initializer' })
    const editor = within(dialog).getByRole('textbox', { name: 'Parameters JSON' })
    await user.clear(editor)
    await user.click(editor)
    await user.paste('{"tags":["extra"]}')
    await user.click(await screen.findByRole('button', { name: 'Save' }))

    expect(defaultProps.onSave).toHaveBeenCalledWith('additional-1', {
      parameters: { tags: ['extra'] },
      order_index: 2,
    })
  })

  it('should call onApply with the saved parameters', async () => {
    const user = userEvent.setup()

    render(
      <TestWrapper>
        <InitializerList {...defaultProps} />
      </TestWrapper>,
    )

    const row = screen.getByTestId('initializer-row-additional-1')
    await user.click(within(row).getByRole('button', { name: 'Apply now' }))

    expect(defaultProps.onApply).toHaveBeenCalledWith('additional-1', 'target', { tags: ['default'] })
  })

  it('should call onRemove with the additional initializer id', async () => {
    const user = userEvent.setup()

    render(
      <TestWrapper>
        <InitializerList {...defaultProps} />
      </TestWrapper>,
    )

    await user.click(within(screen.getByTestId('initializer-row-additional-1')).getByRole('button', { name: 'Remove' }))

    expect(defaultProps.onRemove).toHaveBeenCalledWith('additional-1')
  })

  it('should show a validation error for invalid JSON in the edit dialog', async () => {
    const user = userEvent.setup()

    render(
      <TestWrapper>
        <InitializerList {...defaultProps} />
      </TestWrapper>,
    )

    const row = screen.getByTestId('initializer-row-additional-1')
    await user.click(within(row).getByRole('button', { name: 'Edit' }))

    const dialog = await screen.findByRole('dialog', { name: 'Edit target initializer' })
    const editor = within(dialog).getByRole('textbox', { name: 'Parameters JSON' })
    await user.clear(editor)
    await user.click(editor)
    await user.paste('{"tags":')
    await user.click(await screen.findByRole('button', { name: 'Save' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Unexpected end of JSON input')
    expect(defaultProps.onSave).not.toHaveBeenCalled()
  })
})
