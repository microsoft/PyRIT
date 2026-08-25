import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import type { RegisteredInitializer } from '@/types'

import AvailableInitializersDialog from './AvailableInitializersDialog'

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

const initializers: RegisteredInitializer[] = [
  {
    initializer_name: 'refresh_datasets',
    initializer_type: 'DatasetInitializer',
    description: 'Refreshes datasets.',
    required_env_vars: ['HF_TOKEN'],
    supported_parameters: [
      { name: 'days', type_name: 'int', required: true, default: null, choices: null, is_list: false },
    ],
  },
  {
    initializer_name: 'scorer',
    initializer_type: 'ScorerInitializer',
    description: null,
    required_env_vars: [],
    supported_parameters: [],
  },
]

describe('AvailableInitializersDialog', () => {
  it('disables the trigger button when disabled is set', () => {
    render(
      <TestWrapper>
        <AvailableInitializersDialog registeredInitializers={initializers} disabled />
      </TestWrapper>,
    )

    expect(screen.getByRole('button', { name: /browse available initializers/i })).toBeDisabled()
  })

  it('opens the dialog and lists each initializer with its parameter summary', async () => {
    const user = userEvent.setup()
    render(
      <TestWrapper>
        <AvailableInitializersDialog registeredInitializers={initializers} />
      </TestWrapper>,
    )

    await user.click(screen.getByRole('button', { name: /browse available initializers/i }))

    const dialog = await screen.findByRole('dialog', { hidden: true })
    const refreshRow = within(dialog).getByTestId('available-initializer-row-refresh_datasets')
    expect(within(refreshRow).getByText('Refreshes datasets.')).toBeInTheDocument()
    expect(within(refreshRow).getByText(/HF_TOKEN/)).toBeInTheDocument()
    expect(within(refreshRow).getByText('days (int, required)')).toBeInTheDocument()

    const scorerRow = within(dialog).getByTestId('available-initializer-row-scorer')
    expect(within(scorerRow).getByText('No description available.')).toBeInTheDocument()
    expect(within(scorerRow).getByText('No declared parameters.')).toBeInTheDocument()
  })

  it('shows an empty state when no initializers are registered', async () => {
    const user = userEvent.setup()
    render(
      <TestWrapper>
        <AvailableInitializersDialog registeredInitializers={[]} />
      </TestWrapper>,
    )

    await user.click(screen.getByRole('button', { name: /browse available initializers/i }))

    const dialog = await screen.findByRole('dialog', { hidden: true })
    expect(within(dialog).getByText('No registered initializers were found.')).toBeInTheDocument()
  })
})
