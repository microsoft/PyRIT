import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import { initializersApi } from '@/services/api'
import type {
  AdditionalInitializerSetting,
  BaselineInitializerSetting,
  InitializerSettingsResponse,
  RegisteredInitializer,
} from '@/types'

import InitializerConfig from './InitializerConfig'

jest.mock('@/services/api', () => ({
  initializersApi: {
    getSettings: jest.fn(),
    listRegistered: jest.fn(),
    createAdditional: jest.fn(),
    updateAdditional: jest.fn(),
    deleteAdditional: jest.fn(),
    applyNow: jest.fn(),
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

const scorerInitializer: RegisteredInitializer = {
  initializer_name: 'scorer',
  initializer_type: 'ScorerInitializer',
  description: 'Registers scorers.',
  required_env_vars: [],
  supported_parameters: [],
}

const baselineItem: BaselineInitializerSetting = {
  initializer: targetInitializer,
  parameters: { tags: ['baseline'] },
  order_index: 0,
}

const additionalItem: AdditionalInitializerSetting = {
  id: 'additional-1',
  initializer: scorerInitializer,
  parameters: { mode: 'strict' },
  order_index: 10,
}

const sampleSettings: InitializerSettingsResponse = {
  baseline: [baselineItem],
  additional: [additionalItem],
}

function renderInitializerConfig(): void {
  render(
    <TestWrapper>
      <InitializerConfig />
    </TestWrapper>,
  )
}

describe('InitializerConfig', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockedInitializersApi.getSettings.mockResolvedValue(sampleSettings)
    mockedInitializersApi.listRegistered.mockResolvedValue({
      items: [targetInitializer, scorerInitializer],
      pagination: { limit: 200, has_more: false },
    })
    mockedInitializersApi.createAdditional.mockResolvedValue({
      id: 'additional-2',
      initializer_name: 'target',
      parameters: null,
      order_index: null,
    })
    mockedInitializersApi.updateAdditional.mockResolvedValue({
      id: 'additional-1',
      initializer_name: 'scorer',
      parameters: { mode: 'relaxed' },
      order_index: 11,
    })
    mockedInitializersApi.deleteAdditional.mockResolvedValue()
    mockedInitializersApi.applyNow.mockResolvedValue({
      initializer_name: 'scorer',
      status: 'applied',
      applied_parameters: { mode: 'strict' },
    })
  })

  it('should show loading state initially', () => {
    mockedInitializersApi.getSettings.mockReturnValue(new Promise(() => {}))

    renderInitializerConfig()

    expect(screen.getByText('Loading initializer settings...')).toBeInTheDocument()
  })

  it('should render baseline and additional initializers', async () => {
    renderInitializerConfig()

    expect(await screen.findByRole('heading', { level: 1, name: 'Initializers' })).toBeInTheDocument()
    expect(await screen.findByRole('heading', { level: 2, name: 'Baseline initializers' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'Additional initializers' })).toBeInTheDocument()
    expect(screen.getByTestId('baseline-initializer-row-target')).toHaveTextContent('Registers targets.')
    expect(screen.getByTestId('initializer-row-additional-1')).toHaveTextContent('scorer')
  })

  it('should refresh settings when the refresh button is clicked', async () => {
    const user = userEvent.setup()
    renderInitializerConfig()

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

  it('should create an additional initializer and show success feedback', async () => {
    const user = userEvent.setup()
    renderInitializerConfig()

    await screen.findByTestId('initializer-row-additional-1')
    await user.type(screen.getByRole('combobox', { name: 'Add initializer' }), 'target')
    await user.click(screen.getByRole('button', { name: 'Add' }))

    await waitFor(() => {
      expect(mockedInitializersApi.createAdditional).toHaveBeenCalledWith({ initializer_name: 'target' })
      expect(screen.getByText('Added target.')).toBeInTheDocument()
    })
  })

  it('should save an additional initializer and show success feedback', async () => {
    const user = userEvent.setup()
    renderInitializerConfig()

    const row = await screen.findByTestId('initializer-row-additional-1')
    const parametersEditor = within(row).getByRole('textbox', { name: 'Parameters JSON' })
    await user.clear(parametersEditor)
    await user.click(parametersEditor)
    await user.paste('{"mode":"relaxed"}')
    await user.clear(within(row).getByRole('spinbutton', { name: 'Order index' }))
    await user.type(within(row).getByRole('spinbutton', { name: 'Order index' }), '11')
    await user.click(within(row).getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      expect(mockedInitializersApi.updateAdditional).toHaveBeenCalledWith('additional-1', {
        parameters: { mode: 'relaxed' },
        order_index: 11,
      })
      expect(screen.getByText('Saved additional initializer.')).toBeInTheDocument()
    })
  })

  it('should apply baseline and additional initializers', async () => {
    const user = userEvent.setup()
    renderInitializerConfig()

    const baselineRow = await screen.findByTestId('baseline-initializer-row-target')
    await user.click(within(baselineRow).getByRole('button', { name: 'Apply now' }))

    const additionalRow = await screen.findByTestId('initializer-row-additional-1')
    await user.click(within(additionalRow).getByRole('button', { name: 'Apply now' }))

    await waitFor(() => {
      expect(mockedInitializersApi.applyNow).toHaveBeenCalledWith('target', {
        parameters: { tags: ['baseline'] },
      })
      expect(mockedInitializersApi.applyNow).toHaveBeenCalledWith('scorer', {
        parameters: { mode: 'strict' },
      })
      expect(screen.getByText('Applied scorer.')).toBeInTheDocument()
    })
  })

  it('should remove an additional initializer and show success feedback', async () => {
    const user = userEvent.setup()
    renderInitializerConfig()

    const row = await screen.findByTestId('initializer-row-additional-1')
    await user.click(within(row).getByRole('button', { name: 'Remove' }))

    await waitFor(() => {
      expect(mockedInitializersApi.deleteAdditional).toHaveBeenCalledWith('additional-1')
      expect(screen.getByText('Removed additional initializer.')).toBeInTheDocument()
    })
  })
})
