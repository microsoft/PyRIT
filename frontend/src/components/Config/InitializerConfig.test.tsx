import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import { initializersApi } from '@/services/api'

import InitializerConfig from './InitializerConfig'

jest.mock('@/services/api', () => ({
  initializersApi: {
    getSettings: jest.fn(),
    updateSettings: jest.fn(),
    clearSettings: jest.fn(),
    applyNow: jest.fn(),
  },
}))

jest.mock('./InitializerTable', () => {
  const MockInitializerTable = ({
    items,
    onSave,
    onApply,
    onReset,
  }: {
    items: Array<{ initializer_name: string }>
    onSave: (initializerName: string, request: { enabled: boolean }) => Promise<void>
    onApply: (initializerName: string, parameters?: Record<string, unknown> | null) => Promise<void>
    onReset: (initializerName: string) => Promise<void>
  }) => (
    <div data-testid="initializer-table">
      <span data-testid="initializer-count">{items.length}</span>
      <button onClick={() => void onSave('target', { enabled: false })}>Save target</button>
      <button onClick={() => void onApply('target', { tags: ['extra'] })}>Apply target</button>
      <button onClick={() => void onReset('target')}>Reset target</button>
    </div>
  )
  MockInitializerTable.displayName = 'MockInitializerTable'
  return {
    __esModule: true,
    default: MockInitializerTable,
  }
})

const mockedInitializersApi = initializersApi as jest.Mocked<typeof initializersApi>

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

const SAMPLE_RESPONSE = {
  items: [
    {
      initializer_name: 'target',
      initializer_type: 'TargetInitializer',
      description: 'Registers targets.',
      required_env_vars: [],
      supported_parameters: [],
      enabled: true,
      parameters: null,
      order_index: 0,
      saved_order_index: null,
      source: 'baseline' as const,
    },
  ],
}

describe('InitializerConfig', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockedInitializersApi.getSettings.mockResolvedValue(SAMPLE_RESPONSE)
    mockedInitializersApi.updateSettings.mockResolvedValue({
      initializer_name: 'target',
      enabled: false,
      parameters: null,
      order_index: null,
    })
    mockedInitializersApi.applyNow.mockResolvedValue({
      initializer_name: 'target',
      status: 'applied',
      applied_parameters: { tags: ['extra'] },
    })
    mockedInitializersApi.clearSettings.mockResolvedValue()
  })

  it('should show loading state initially', () => {
    mockedInitializersApi.getSettings.mockReturnValue(new Promise(() => {}))

    render(
      <TestWrapper>
        <InitializerConfig />
      </TestWrapper>,
    )

    expect(screen.getByText('Loading initializer settings...')).toBeInTheDocument()
  })

  it('should render fetched initializer settings', async () => {
    render(
      <TestWrapper>
        <InitializerConfig />
      </TestWrapper>,
    )

    await waitFor(() => {
      expect(screen.getByTestId('initializer-table')).toBeInTheDocument()
      expect(screen.getByTestId('initializer-count')).toHaveTextContent('1')
    })
  })

  it('should refresh settings when the refresh button is clicked', async () => {
    const user = userEvent.setup()

    render(
      <TestWrapper>
        <InitializerConfig />
      </TestWrapper>,
    )

    await waitFor(() => {
      expect(mockedInitializersApi.getSettings).toHaveBeenCalledTimes(1)
    })

    await user.click(screen.getByRole('button', { name: 'Refresh' }))

    await waitFor(() => {
      expect(mockedInitializersApi.getSettings).toHaveBeenCalledTimes(2)
    })
  })

  it('should save initializer settings and show success feedback', async () => {
    const user = userEvent.setup()

    render(
      <TestWrapper>
        <InitializerConfig />
      </TestWrapper>,
    )

    await waitFor(() => {
      expect(screen.getByTestId('initializer-table')).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: 'Save target' }))

    await waitFor(() => {
      expect(mockedInitializersApi.updateSettings).toHaveBeenCalledWith('target', { enabled: false })
      expect(screen.getByText('Saved settings for target.')).toBeInTheDocument()
    })
  })

  it('should apply an initializer and show success feedback', async () => {
    const user = userEvent.setup()

    render(
      <TestWrapper>
        <InitializerConfig />
      </TestWrapper>,
    )

    await waitFor(() => {
      expect(screen.getByTestId('initializer-table')).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: 'Apply target' }))

    await waitFor(() => {
      expect(mockedInitializersApi.applyNow).toHaveBeenCalledWith('target', { parameters: { tags: ['extra'] } })
      expect(screen.getByText('Applied target.')).toBeInTheDocument()
    })
  })

  it('should clear saved settings and show success feedback', async () => {
    const user = userEvent.setup()

    render(
      <TestWrapper>
        <InitializerConfig />
      </TestWrapper>,
    )

    await waitFor(() => {
      expect(screen.getByTestId('initializer-table')).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: 'Reset target' }))

    await waitFor(() => {
      expect(mockedInitializersApi.clearSettings).toHaveBeenCalledWith('target')
      expect(screen.getByText('Cleared saved settings for target.')).toBeInTheDocument()
    })
  })
})
