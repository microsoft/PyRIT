import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import { initializersApi } from '@/services/api'
import type {
  ConfiguredInitializerSetting,
  InitializerSettingsResponse,
  RegisteredInitializer,
} from '@/types'

import Initializers from './Initializers'

jest.mock('@/services/api', () => ({
  initializersApi: {
    getSettings: jest.fn(),
    listRegistered: jest.fn(),
  },
}))

const mockedInitializersApi = initializersApi as jest.Mocked<typeof initializersApi>

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

const configuredItem: ConfiguredInitializerSetting = {
  initializer_name: 'target',
  parameters: { tags: ['configured'] },
  order_index: 0,
}

const sampleSettings: InitializerSettingsResponse = {
  configured: [configuredItem],
}

function renderInitializers(): void {
  render(
    <TestWrapper>
      <Initializers />
    </TestWrapper>,
  )
}

describe('Initializers', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockedInitializersApi.getSettings.mockResolvedValue(sampleSettings)
    mockedInitializersApi.listRegistered.mockResolvedValue({
      items: [targetInitializer],
      pagination: { limit: 200, has_more: false },
    })
  })

  it('should show loading state initially', () => {
    mockedInitializersApi.getSettings.mockReturnValue(new Promise(() => {}))

    renderInitializers()

    expect(screen.getByText('Loading initializer settings...')).toBeInTheDocument()
  })

  it('should render initializers configured in .pyrit_conf', async () => {
    renderInitializers()

    expect(await screen.findByRole('heading', { level: 2, name: 'Configured initializers' })).toBeInTheDocument()
    expect(screen.getByTestId('configured-initializer-row-0')).toHaveTextContent('Registers targets.')
  })

  it('should refresh settings when the refresh button is clicked', async () => {
    const user = userEvent.setup()
    renderInitializers()

    await waitFor(() => {
      expect(mockedInitializersApi.getSettings).toHaveBeenCalledTimes(1)
      expect(mockedInitializersApi.listRegistered).toHaveBeenCalledTimes(1)
    })

    await user.click(screen.getByRole('button', { name: 'Refresh' }))

    await waitFor(() => {
      expect(mockedInitializersApi.getSettings).toHaveBeenCalledTimes(2)
      expect(mockedInitializersApi.listRegistered).toHaveBeenCalledTimes(2)
    })
  })

  it('should render a read-only catalog of all registered initializers', async () => {
    const user = userEvent.setup()
    renderInitializers()

    await user.click(await screen.findByRole('button', { name: 'Browse available initializers' }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('Available initializers')).toBeInTheDocument()
    expect(screen.getByTestId('available-initializer-row-target')).toHaveTextContent('Registers targets.')
  })

  it('should keep configured settings visible when catalog loading fails', async () => {
    mockedInitializersApi.listRegistered.mockRejectedValue(new Error('Service Unavailable'))

    renderInitializers()

    expect(await screen.findByTestId('configured-initializer-row-0')).toBeInTheDocument()
    expect(screen.getByText('Service Unavailable')).toBeInTheDocument()
  })
})
