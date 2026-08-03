import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import { initializersApi } from '@/services/api'
import type {
  AdditionalInitializerSetting,
  BaselineInitializerSetting,
  InitializerSettingsResponse,
  RegisteredInitializer,
} from '@/types'

import Initializers from './Initializers'

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

async function openDialogByButton(
  _user: ReturnType<typeof userEvent.setup>,
  buttonName: RegExp | string,
  dialogName: string,
): Promise<HTMLElement> {
  const trigger = await screen.findByRole('button', { name: buttonName })
  await waitFor(() => expect(trigger).toBeEnabled())
  fireEvent.click(trigger)
  const dialog = await screen.findByRole('dialog', {}, { timeout: 3000 })
  await within(dialog).findByText(dialogName)
  return dialog
}

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
      name: 'tags',
      type_name: 'list[str]',
      required: false,
      default: null,
      choices: null,
      is_list: true,
      description: 'Scorer tags.',
    },
  ],
}

const baselineItem: BaselineInitializerSetting = {
  initializer_name: 'target',
  parameters: { tags: ['baseline'] },
  order_index: 0,
}

const additionalItem: AdditionalInitializerSetting = {
  id: 'additional-1',
  initializer_name: 'scorer',
  parameters: { mode: 'strict' },
  order_index: 10,
}

const sampleSettings: InitializerSettingsResponse = {
  baseline: [baselineItem],
  additional: [additionalItem],
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

    renderInitializers()

    expect(screen.getByText('Loading initializer settings...')).toBeInTheDocument()
  })

  it('should render baseline and additional initializers', async () => {
    renderInitializers()

    expect(await screen.findByRole('heading', { level: 1, name: 'Initializers' })).toBeInTheDocument()
    expect(await screen.findByRole('heading', { level: 2, name: 'Baseline initializers' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'Additional initializers' })).toBeInTheDocument()
    expect(screen.getByTestId('baseline-initializer-row-target')).toHaveTextContent('Registers targets.')
    expect(screen.getByTestId('initializer-row-additional-1')).toHaveTextContent('scorer')
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

  it('should render a read-only catalog of all registered initializers in a dialog', async () => {
    const user = userEvent.setup()
    renderInitializers()

    await screen.findByRole('button', { name: 'Browse available initializers' })
    await openDialogByButton(user, 'Browse available initializers', 'Available initializers')

    const catalogTarget = screen.getByTestId('available-initializer-row-target')
    expect(catalogTarget).toHaveTextContent('Registers targets.')
    expect(catalogTarget).toHaveTextContent('tags')
    expect(screen.getByTestId('available-initializer-row-scorer')).toBeInTheDocument()
  })

  it('should create the selected initializer and show success feedback', async () => {
    const user = userEvent.setup()
    renderInitializers()

    await screen.findByTestId('initializer-row-additional-1')
    const dialog = await openDialogByButton(user, 'Add initializer', 'Add target initializer')
    expect(dialog).toBeInTheDocument()
    await user.click(await within(dialog).findByRole('button', { name: 'Add', hidden: true }))

    await waitFor(() => {
      expect(mockedInitializersApi.createAdditional).toHaveBeenCalledWith({
        initializer_name: 'target',
        parameters: null,
      })
      expect(screen.getByText('Added target initializer.')).toBeInTheDocument()
    })
  })

  it('should let the user choose a non-target initializer to add', async () => {
    const user = userEvent.setup()
    renderInitializers()

    await screen.findByTestId('initializer-row-additional-1')
    const combobox = screen.getByRole('combobox', { name: 'Initializer to add' })
    await user.selectOptions(combobox, 'scorer')
    await waitFor(() => expect(combobox).toHaveValue('scorer'))
    const dialog = await openDialogByButton(user, /Add initializer|Adding/, 'Add scorer initializer')
    expect(dialog).toBeInTheDocument()
    await user.click(await within(dialog).findByRole('button', { name: 'Add', hidden: true }))

    await waitFor(() => {
      expect(mockedInitializersApi.createAdditional).toHaveBeenCalledWith({
        initializer_name: 'scorer',
        parameters: null,
      })
      expect(screen.getByText('Added scorer initializer.')).toBeInTheDocument()
    })
  })

  it('should save an additional initializer from the edit dialog', async () => {
    const user = userEvent.setup()
    renderInitializers()

    await screen.findByTestId('initializer-row-additional-1')
    const dialog = await openDialogByButton(user, 'Edit', 'Edit scorer initializer')
    fireEvent.change(within(dialog).getByTestId('param-tags'), { target: { value: 'relaxed' } })
    await user.click(await within(dialog).findByRole('button', { name: 'Save', hidden: true }))

    await waitFor(() => {
      expect(mockedInitializersApi.updateAdditional).toHaveBeenCalledWith('additional-1', {
        parameters: { tags: ['relaxed'] },
        order_index: 10,
      })
      expect(screen.getByText('Saved additional initializer.')).toBeInTheDocument()
    })
  })

  it('should apply an additional initializer', async () => {
    const user = userEvent.setup()
    renderInitializers()

    const additionalRow = await screen.findByTestId('initializer-row-additional-1')
    await user.click(within(additionalRow).getByRole('button', { name: 'Apply now' }))

    await waitFor(() => {
      expect(mockedInitializersApi.applyNow).toHaveBeenCalledWith('scorer', {
        parameters: { mode: 'strict' },
      })
      expect(screen.getByText('Applied scorer.')).toBeInTheDocument()
    })
  })

  it('should not render an apply button on baseline initializers', async () => {
    renderInitializers()

    const baselineRow = await screen.findByTestId('baseline-initializer-row-target')
    expect(within(baselineRow).queryByRole('button', { name: 'Apply now' })).not.toBeInTheDocument()
  })

  it('should remove an additional initializer and show success feedback', async () => {
    const user = userEvent.setup()
    renderInitializers()

    const row = await screen.findByTestId('initializer-row-additional-1')
    await user.click(within(row).getByRole('button', { name: 'Remove' }))

    await waitFor(() => {
      expect(mockedInitializersApi.deleteAdditional).toHaveBeenCalledWith('additional-1')
      expect(screen.getByText('Removed additional initializer.')).toBeInTheDocument()
    })
  })
})
