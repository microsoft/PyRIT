import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import type { EffectiveInitializerSetting } from '@/types'

import InitializerList from './InitializerList'

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

const SAMPLE_ITEMS: EffectiveInitializerSetting[] = [
  {
    initializer_name: 'target',
    initializer_type: 'TargetInitializer',
    description: 'Registers targets.',
    required_env_vars: ['AZURE_OPENAI_ENDPOINT'],
    supported_parameters: [
      {
        name: 'tags',
        type_name: 'list[str]',
        required: false,
        default: ['default'],
        choices: null,
        is_list: true,
        description: 'Target tags.',
      },
    ],
    parameters: { tags: ['default'] },
    order_index: 0,
    saved_order_index: 2,
    source: 'baseline+override',
  },
  {
    initializer_name: 'scorer',
    initializer_type: 'ScorerInitializer',
    description: 'Registers scorers.',
    required_env_vars: [],
    supported_parameters: [],
    parameters: null,
    order_index: 1,
    saved_order_index: null,
    source: 'baseline',
  },
]

describe('InitializerList', () => {
  const defaultProps = {
    items: SAMPLE_ITEMS,
    onSave: jest.fn().mockResolvedValue(undefined),
    onApply: jest.fn().mockResolvedValue(undefined),
    onReset: jest.fn().mockResolvedValue(undefined),
  }

  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('should render initializer rows and metadata', () => {
    render(
      <TestWrapper>
        <InitializerList {...defaultProps} />
      </TestWrapper>,
    )

    expect(screen.getByRole('list', { name: 'Target auto-registration' })).toBeInTheDocument()
    expect(screen.getByText('target')).toBeInTheDocument()
    expect(screen.getByText('Customized')).toBeInTheDocument()
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

  it('should call onSave with parsed settings', async () => {
    const user = userEvent.setup()

    render(
      <TestWrapper>
        <InitializerList {...defaultProps} />
      </TestWrapper>,
    )

    const row = screen.getByTestId('initializer-row-target')
    await user.clear(within(row).getByRole('textbox', { name: 'Parameters JSON' }))
    await user.click(within(row).getByRole('textbox', { name: 'Parameters JSON' }))
    await user.paste('{"tags":["extra"]}')
    await user.click(within(row).getByRole('button', { name: 'Save' }))

    expect(defaultProps.onSave).toHaveBeenCalledWith('target', {
      parameters: { tags: ['extra'] },
      order_index: 2,
    })
  })

  it('should disable Save until parameters change, while keeping Apply now enabled', async () => {
    const user = userEvent.setup()

    render(
      <TestWrapper>
        <InitializerList {...defaultProps} />
      </TestWrapper>,
    )

    const row = screen.getByTestId('initializer-row-target')
    expect(within(row).getByRole('button', { name: 'Save' })).toBeDisabled()
    expect(within(row).getByRole('button', { name: 'Apply now' })).toBeEnabled()

    await user.clear(within(row).getByRole('textbox', { name: 'Parameters JSON' }))
    await user.click(within(row).getByRole('textbox', { name: 'Parameters JSON' }))
    await user.paste('{"tags":["extra"]}')

    expect(within(row).getByRole('button', { name: 'Save' })).toBeEnabled()
    expect(within(row).getByRole('button', { name: 'Apply now' })).toBeEnabled()
  })

  it('should call onApply with parsed parameters', async () => {
    const user = userEvent.setup()

    render(
      <TestWrapper>
        <InitializerList {...defaultProps} />
      </TestWrapper>,
    )

    const row = screen.getByTestId('initializer-row-scorer')
    await user.clear(within(row).getByRole('textbox', { name: 'Parameters JSON' }))
    await user.click(within(row).getByRole('textbox', { name: 'Parameters JSON' }))
    await user.paste('{"mode":"strict"}')
    await user.click(within(row).getByRole('button', { name: 'Apply now' }))

    expect(defaultProps.onApply).toHaveBeenCalledWith('scorer', { mode: 'strict' })
  })

  it('should show a validation error for invalid JSON', async () => {
    const user = userEvent.setup()

    render(
      <TestWrapper>
        <InitializerList {...defaultProps} />
      </TestWrapper>,
    )

    const row = screen.getByTestId('initializer-row-target')
    await user.clear(within(row).getByRole('textbox', { name: 'Parameters JSON' }))
    await user.click(within(row).getByRole('textbox', { name: 'Parameters JSON' }))
    await user.paste('{"tags":')
    await user.click(within(row).getByRole('button', { name: 'Save' }))

    expect(await within(row).findByRole('alert')).toHaveTextContent('Unexpected end of JSON input')
    expect(defaultProps.onSave).not.toHaveBeenCalled()
  })

  it('should only show reset action for saved overrides', () => {
    render(
      <TestWrapper>
        <InitializerList {...defaultProps} />
      </TestWrapper>,
    )

    expect(screen.getByRole('button', { name: 'Reset saved' })).toBeInTheDocument()
    const baselineRow = screen.getByTestId('initializer-row-scorer')
    expect(within(baselineRow).queryByRole('button', { name: 'Reset saved' })).not.toBeInTheDocument()
  })
})

