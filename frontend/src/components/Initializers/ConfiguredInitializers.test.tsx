import { render, screen, within } from '@testing-library/react'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import type { ConfiguredInitializerSetting, RegisteredInitializer } from '@/types'

import ConfiguredInitializers from './ConfiguredInitializers'

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

const registeredInitializers: RegisteredInitializer[] = [
  {
    initializer_name: 'target',
    initializer_type: 'TargetInitializer',
    description: 'Registers targets.',
    required_env_vars: ['AZURE_OPENAI_ENDPOINT', 'AZURE_OPENAI_KEY'],
    supported_parameters: [],
  },
]

describe('ConfiguredInitializers', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('renders the empty state when no initializers are configured', () => {
    render(
      <TestWrapper>
        <ConfiguredInitializers
          items={[]}
          registeredInitializers={registeredInitializers}
        />
      </TestWrapper>,
    )

    expect(screen.getByText('No initializers are configured in .pyrit_conf.')).toBeInTheDocument()
    expect(screen.queryByRole('list', { name: 'Configured initializers' })).not.toBeInTheDocument()
  })

  it('renders each configured row with description, env vars, order, and parameters', () => {
    const items: ConfiguredInitializerSetting[] = [
      { initializer_name: 'target', parameters: { tags: ['default'] }, order_index: 0 },
    ]

    render(
      <TestWrapper>
        <ConfiguredInitializers
          items={items}
          registeredInitializers={registeredInitializers}
        />
      </TestWrapper>,
    )

    const row = screen.getByTestId('configured-initializer-row-0')
    expect(within(row).getByText('target')).toBeInTheDocument()
    expect(within(row).getByText('Registers targets.')).toBeInTheDocument()
    expect(within(row).getByText(/AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY/)).toBeInTheDocument()
    expect(within(row).getByText('Order: 0')).toBeInTheDocument()
    expect(within(row).getByText(/"tags"/)).toBeInTheDocument()
  })

  it('falls back to a placeholder for a name that is no longer registered', () => {
    const items: ConfiguredInitializerSetting[] = [
      { initializer_name: 'ghost', parameters: null, order_index: 1 },
    ]

    render(
      <TestWrapper>
        <ConfiguredInitializers
          items={items}
          registeredInitializers={registeredInitializers}
        />
      </TestWrapper>,
    )

    const row = screen.getByTestId('configured-initializer-row-1')
    expect(within(row).getByText('Initializer is no longer registered.')).toBeInTheDocument()
    expect(within(row).getByText(/Required env vars: None/)).toBeInTheDocument()
  })
})
