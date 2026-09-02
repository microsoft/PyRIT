import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { configurationApi, initializersApi } from '@/services/api'

import Configuration from './Configuration'

jest.mock('@/services/api', () => ({
  configurationApi: {
    getContent: jest.fn(),
    updateContent: jest.fn(),
    listEnvironmentFiles: jest.fn(),
    getEnvironmentFile: jest.fn(),
    updateEnvironmentFile: jest.fn(),
  },
  initializersApi: {
    getSettings: jest.fn(),
    listRegistered: jest.fn(),
    listCustom: jest.fn(),
    register: jest.fn(),
    unregister: jest.fn(),
  },
}))

const mockedConfigurationApi = jest.mocked(configurationApi)
const mockedInitializersApi = jest.mocked(initializersApi)

function renderPage(): void {
  render(
    <FluentProvider theme={webLightTheme}>
      <Configuration />
    </FluentProvider>,
  )
}

describe('Configuration', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockedConfigurationApi.getContent.mockResolvedValue({
      content: 'operator: alice\n',
      source: 'C:/Users/test/.pyrit/config.yaml',
      version: 'config-v1',
    })
    mockedConfigurationApi.listEnvironmentFiles.mockResolvedValue({
      items: [
        { id: '0', name: '.env', path: 'C:/Users/test/.pyrit/.env', content: '', exists: true, version: 'v1' },
        { id: '1', name: '.env.local', path: 'C:/Users/test/.pyrit/.env.local', content: '', exists: false, version: 'v1' },
      ],
    })
    mockedConfigurationApi.getEnvironmentFile.mockResolvedValue({
      id: '0',
      name: '.env',
      path: 'C:/Users/test/.pyrit/.env',
      content: 'API_KEY=value\n',
      exists: true,
      version: 'v1',
    })
    mockedInitializersApi.listCustom.mockResolvedValue({
      source: 'C:/Users/test/.pyrit/custom_initializers',
      items: [{
        initializer_name: 'custom_target',
        script_content: 'class CustomTargetInitializer: pass',
        source: 'C:/Users/test/.pyrit/custom_initializers/custom_target.py',
      }],
    })
    mockedInitializersApi.register.mockResolvedValue()
    mockedInitializersApi.unregister.mockResolvedValue()
    mockedInitializersApi.getSettings.mockResolvedValue({
      configured: [{ initializer_name: 'target', parameters: { tags: ['default'] }, order_index: 0 }],
    })
    mockedInitializersApi.listRegistered.mockResolvedValue({
      items: [{
        initializer_name: 'target',
        initializer_type: 'TargetInitializer',
        description: 'Registers targets.',
        required_env_vars: [],
        supported_parameters: [],
      }],
      pagination: { limit: 200, has_more: false },
    })
  })

  it('should load and display configuration content', async () => {
    renderPage()

    expect(screen.getByRole('heading', { level: 1, name: 'Configuration' })).toBeInTheDocument()
    expect(await screen.findByLabelText('Configuration YAML')).toHaveValue('operator: alice\n')
    expect(screen.getByRole('navigation', { name: 'Configuration files' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /\.pyrit_conf/i })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByText('C:/Users/test/.pyrit/config.yaml', { selector: 'label' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Copy YAML source' })).toBeInTheDocument()
    expect(screen.getByTestId('yaml-highlight').innerHTML).toContain('token key atrule')
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
  })

  it('should save edited configuration content', async () => {
    const user = userEvent.setup()
    mockedConfigurationApi.updateContent.mockResolvedValue({
      content: 'operator: bob\n',
      source: 'C:/Users/test/.pyrit/config.yaml',
      version: 'config-v2',
    })
    renderPage()

    const editor = await screen.findByLabelText('Configuration YAML')
    await user.clear(editor)
    await user.type(editor, 'operator: bob\n')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(mockedConfigurationApi.updateContent).toHaveBeenCalledWith({
      content: 'operator: bob\n',
      version: 'config-v1',
    })
    expect(await screen.findByText(/restart PyRIT/i)).toBeInTheDocument()
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
      version: 'v2',
    })
    renderPage()

    await user.click(screen.getByRole('tab', { name: 'Environment & Secrets' }))
    const editor = await screen.findByLabelText('Environment file contents')
    expect(screen.getByText('C:/Users/test/.pyrit/.env', { selector: 'label' })).toBeInTheDocument()
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
      version: 'v1',
    })
  })

  it('should display and update an AKV environment source', async () => {
    const user = userEvent.setup()
    const secretUrl = 'https://vault.vault.azure.net/secrets/bootstrap'
    mockedConfigurationApi.listEnvironmentFiles.mockResolvedValue({
      items: [
        { id: 'akv:0', name: 'AKV: bootstrap', path: secretUrl, content: '', exists: true, version: 'v1' },
      ],
    })
    mockedConfigurationApi.getEnvironmentFile.mockResolvedValue({
      id: 'akv:0',
      name: 'AKV: bootstrap',
      path: secretUrl,
      content: 'API_KEY=before\n',
      exists: true,
      version: 'v1',
    })
    mockedConfigurationApi.updateEnvironmentFile.mockResolvedValue({
      id: 'akv:0',
      name: 'AKV: bootstrap',
      path: secretUrl,
      content: 'API_KEY=after\n',
      exists: true,
      version: 'v2',
    })
    renderPage()

    await user.click(screen.getByRole('tab', { name: 'Environment & Secrets' }))
    expect(await screen.findByRole('button', { name: /AKV: bootstrap/i })).toBeInTheDocument()
    expect(screen.getByTitle(secretUrl)).toBeInTheDocument()
    const editor = await screen.findByLabelText('Environment file contents')
    expect(screen.getByText(secretUrl, { selector: 'label' })).toBeInTheDocument()
    await user.clear(editor)
    await user.type(editor, 'API_KEY=after\n')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(mockedConfigurationApi.updateEnvironmentFile).toHaveBeenCalledWith('akv:0', {
      content: 'API_KEY=after\n',
      version: 'v1',
    })
  })

  it('should list and register custom initializers', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('tab', { name: 'Custom Initializers' }))
    expect(await screen.findByText(
      'C:/Users/test/.pyrit/custom_initializers/custom_target.py',
      { selector: 'label' },
    )).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Add initializer' }))
    const dialog = screen.getByRole('dialog', { name: 'Add custom initializer' })
    await user.type(within(dialog).getByRole('textbox', { name: /Initializer name/ }), 'new_custom')
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Python source' }), {
      target: { value: 'class NewCustom: pass' },
    })
    await user.click(within(dialog).getByRole('button', { name: 'Add' }))

    await waitFor(() => {
      expect(mockedInitializersApi.register).toHaveBeenCalledWith({
        name: 'new_custom',
        script_content: 'class NewCustom: pass',
      })
    })
  })

  it('should show configured initializers without a runtime apply action', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('tab', { name: 'Initializers' }))

    expect(await screen.findByTestId('configured-initializer-row-0')).toHaveTextContent('Registers targets.')
    expect(screen.queryByRole('button', { name: 'Apply now' })).not.toBeInTheDocument()
  })

})
