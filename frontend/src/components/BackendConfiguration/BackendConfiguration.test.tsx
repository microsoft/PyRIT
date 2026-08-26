import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { configurationApi, initializersApi } from '@/services/api'

import BackendConfiguration from './BackendConfiguration'

jest.mock('@/services/api', () => ({
  configurationApi: {
    getContent: jest.fn(),
    updateContent: jest.fn(),
    listEnvironmentFiles: jest.fn(),
    getEnvironmentFile: jest.fn(),
    updateEnvironmentFile: jest.fn(),
  },
  initializersApi: {
    listCustom: jest.fn(),
    register: jest.fn(),
    updateCustom: jest.fn(),
    unregister: jest.fn(),
  },
}))

const mockedConfigurationApi = jest.mocked(configurationApi)
const mockedInitializersApi = jest.mocked(initializersApi)

function renderPage(): void {
  render(
    <FluentProvider theme={webLightTheme}>
      <BackendConfiguration />
    </FluentProvider>,
  )
}

describe('BackendConfiguration', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockedConfigurationApi.getContent.mockResolvedValue({
      content: 'operator: alice\n',
      source: 'C:/Users/test/.pyrit/config.yaml',
    })
    mockedConfigurationApi.listEnvironmentFiles.mockResolvedValue({
      items: [
        { id: '0', name: '.env', path: 'C:/Users/test/.pyrit/.env', content: '', exists: true },
        { id: '1', name: '.env.local', path: 'C:/Users/test/.pyrit/.env.local', content: '', exists: false },
      ],
    })
    mockedConfigurationApi.getEnvironmentFile.mockResolvedValue({
      id: '0',
      name: '.env',
      path: 'C:/Users/test/.pyrit/.env',
      content: 'API_KEY=value\n',
      exists: true,
    })
    mockedInitializersApi.listCustom.mockResolvedValue({
      source: 'C:/Users/test/.pyrit/custom_initializers',
      items: [
        {
          initializer_name: 'custom_target',
          script_content: 'class CustomTargetInitializer: pass',
          source: 'C:/Users/test/.pyrit/custom_initializers/custom_target.py',
        },
      ],
    })
    mockedInitializersApi.register.mockResolvedValue({
      initializer_name: 'new_custom',
      initializer_type: 'NewCustomInitializer',
      description: 'New custom initializer.',
      required_env_vars: [],
      supported_parameters: [],
    })
    mockedInitializersApi.updateCustom.mockResolvedValue({
      initializer_name: 'custom_target',
      initializer_type: 'CustomTargetInitializer',
      description: 'Updated custom initializer.',
      required_env_vars: [],
      supported_parameters: [],
    })
    mockedInitializersApi.unregister.mockResolvedValue()
  })

  it('should load and display configuration content', async () => {
    renderPage()

    expect(await screen.findByLabelText('Configuration YAML')).toHaveValue('operator: alice\n')
    expect(screen.getByRole('navigation', { name: 'Configuration files' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /\.pyrit_conf/i })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByText('C:/Users/test/.pyrit/config.yaml')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Copy YAML source' })).toBeInTheDocument()
    expect(screen.getByTestId('yaml-highlight').innerHTML).toContain('token key atrule')
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
  })

  it('should save edited configuration content', async () => {
    const user = userEvent.setup()
    mockedConfigurationApi.updateContent.mockResolvedValue({
      content: 'operator: bob\n',
      source: 'C:/Users/test/.pyrit/config.yaml',
    })
    renderPage()

    const editor = await screen.findByLabelText('Configuration YAML')
    await user.clear(editor)
    await user.type(editor, 'operator: bob\n')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(mockedConfigurationApi.updateContent).toHaveBeenCalledWith({ content: 'operator: bob\n' })
    expect(await screen.findByText(/restart the backend/i)).toBeInTheDocument()
  })

  it('should show a load error', async () => {
    mockedConfigurationApi.getContent.mockRejectedValue(new Error('Configuration unavailable'))
    renderPage()

    expect(await screen.findByText('Configuration unavailable')).toBeInTheDocument()
  })

  it('should edit and save a selected environment file with dotenv highlighting', async () => {
    const user = userEvent.setup()
    mockedConfigurationApi.updateEnvironmentFile.mockResolvedValue({
      id: '0',
      name: '.env',
      path: 'C:/Users/test/.pyrit/.env',
      content: 'API_KEY=updated\n',
      exists: true,
    })
    renderPage()

    await user.click(screen.getByRole('tab', { name: 'Environment Files' }))
    const editor = await screen.findByLabelText('Environment file contents')
    expect(screen.getByTitle('C:/Users/test/.pyrit/.env')).toBeInTheDocument()
    expect(editor).toHaveValue('API_KEY=value\n')
    expect(screen.getByTestId('dotenv-highlight').innerHTML).toContain('token key atrule')
    expect(screen.getByRole('button', { name: 'Copy dotenv source' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /\.env\.local/i })).toHaveTextContent('(new)')

    await user.clear(editor)
    await user.type(editor, 'API_KEY=updated\n')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(mockedConfigurationApi.updateEnvironmentFile).toHaveBeenCalledWith('0', {
      content: 'API_KEY=updated\n',
    })
  })

  it('should display and update an AKV environment source', async () => {
    const user = userEvent.setup()
    const secretUrl = 'https://vault.vault.azure.net/secrets/bootstrap'
    mockedConfigurationApi.listEnvironmentFiles.mockResolvedValue({
      items: [
        { id: 'akv:0', name: 'AKV: bootstrap', path: secretUrl, content: '', exists: true },
      ],
    })
    mockedConfigurationApi.getEnvironmentFile.mockResolvedValue({
      id: 'akv:0',
      name: 'AKV: bootstrap',
      path: secretUrl,
      content: 'API_KEY=before\n',
      exists: true,
    })
    mockedConfigurationApi.updateEnvironmentFile.mockResolvedValue({
      id: 'akv:0',
      name: 'AKV: bootstrap',
      path: secretUrl,
      content: 'API_KEY=after\n',
      exists: true,
    })
    renderPage()

    await user.click(screen.getByRole('tab', { name: 'Environment Files' }))
    expect(await screen.findByRole('button', { name: /AKV: bootstrap/i })).toBeInTheDocument()
    expect(screen.getByTitle(secretUrl)).toBeInTheDocument()
    const editor = await screen.findByLabelText('Environment file contents')
    await user.clear(editor)
    await user.type(editor, 'API_KEY=after\n')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(mockedConfigurationApi.updateEnvironmentFile).toHaveBeenCalledWith('akv:0', {
      content: 'API_KEY=after\n',
    })
  })

  it('should edit storage-backed custom initializers in its own tab', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('tab', { name: 'Custom Initializers' }))
    expect(screen.getByText('C:/Users/test/.pyrit/custom_initializers/custom_target.py')).toBeInTheDocument()
    expect(await screen.findByRole('navigation', { name: 'Custom initializer files' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Copy Python source' })).toBeInTheDocument()
    fireEvent.change(screen.getByRole('textbox', { name: 'Python source' }), {
      target: { value: 'class UpdatedCustomTargetInitializer: pass' },
    })
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      expect(mockedInitializersApi.updateCustom).toHaveBeenCalledWith('custom_target', {
        script_content: 'class UpdatedCustomTargetInitializer: pass',
      })
    })
    expect(await screen.findByText('Updated custom_target.')).toBeInTheDocument()
  })

  it('should register a storage-backed custom initializer', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('tab', { name: 'Custom Initializers' }))
    await user.click(screen.getByRole('button', { name: 'Register initializer' }))
    const registerDialog = await screen.findByRole('dialog')
    await user.type(within(registerDialog).getByRole('textbox', { name: /Initializer name/ }), 'new_custom')
    fireEvent.change(within(registerDialog).getByRole('textbox', { name: 'Python source' }), {
      target: { value: 'class NewCustom: pass' },
    })
    await user.click(within(registerDialog).getByRole('button', { name: 'Register' }))

    await waitFor(() => {
      expect(mockedInitializersApi.register).toHaveBeenCalledWith({
        name: 'new_custom',
        script_content: 'class NewCustom: pass',
      })
    })
  })
})