import type { ReactElement } from 'react'

import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider, useLocation, useNavigate } from 'react-router'

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
const originalRequest = globalThis.Request

class RouterTestRequest {
  readonly url: string
  readonly method: string
  readonly signal: AbortSignal

  constructor(input: string | URL, init?: { method?: string, signal?: AbortSignal | null }) {
    this.url = String(input)
    this.method = init?.method ?? 'GET'
    this.signal = init?.signal ?? new AbortController().signal
  }
}

// Fluent UI dialogs can render slowly in JSDOM under full test load.
jest.setTimeout(60_000)

function RouterProbe(): ReactElement {
  const location = useLocation()
  const navigate = useNavigate()

  return (
    <>
      <output aria-label="Current URL">{location.pathname}{location.search}</output>
      <button type="button" onClick={() => void navigate(-1)}>Go back</button>
      <button type="button" onClick={() => void navigate('/scanner')}>Go to scanner</button>
    </>
  )
}

function renderPage(
  initialPath = '/config',
  previousEntries: string[] = [],
): ReturnType<typeof createMemoryRouter> {
  const router = createMemoryRouter([
    {
      path: '/config',
      element: (
        <>
          <main>
            <Configuration />
          </main>
          <RouterProbe />
        </>
      ),
    },
    {
      path: '*',
      element: (
        <>
          <h1>Other page</h1>
          <RouterProbe />
        </>
      ),
    },
  ], {
    initialEntries: [...previousEntries, initialPath],
    initialIndex: previousEntries.length,
  })

  render(
    <FluentProvider theme={webLightTheme}>
      <RouterProvider router={router} />
    </FluentProvider>,
  )
  return router
}

describe('Configuration', () => {
  beforeAll(() => {
    Object.defineProperty(globalThis, 'Request', {
      configurable: true,
      writable: true,
      value: RouterTestRequest,
    })
  })

  afterAll(() => {
    Object.defineProperty(globalThis, 'Request', {
      configurable: true,
      writable: true,
      value: originalRequest,
    })
  })

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

    expect(screen.getAllByRole('main')).toHaveLength(1)
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
    const dialog = await screen.findByRole(
      'dialog',
      { name: 'Add custom initializer' },
      { timeout: 15_000 },
    )
    const nameInput = await within(dialog).findByRole(
      'textbox',
      { name: /Initializer name/ },
      { timeout: 15_000 },
    )
    await user.type(nameInput, 'new_custom')
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

  it('should restore a linked tab and preserve unrelated query parameters', async () => {
    const user = userEvent.setup()
    renderPage('/config?source=docs&tab=environment')

    expect(screen.getByRole('tab', { name: 'Environment & Secrets', selected: true })).toBeInTheDocument()
    expect(await screen.findByLabelText('Environment file contents')).toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: 'Initializers' }))
    expect(screen.getByLabelText('Current URL')).toHaveTextContent(/^\/config\?source=docs&tab=initializers$/)

    await user.click(screen.getByRole('tab', { name: 'PyRIT Configuration' }))
    expect(screen.getByLabelText('Current URL')).toHaveTextContent(/^\/config\?source=docs$/)
  })

  it('should restore the previous tab through browser history', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('tab', { name: 'Environment & Secrets' }))
    await user.click(screen.getByRole('tab', { name: 'Initializers' }))
    expect(screen.getByLabelText('Current URL')).toHaveTextContent(/^\/config\?tab=initializers$/)

    await user.click(screen.getByRole('button', { name: 'Go back' }))
    expect(screen.getByRole('tab', { name: 'Environment & Secrets', selected: true })).toBeInTheDocument()
    expect(screen.getByLabelText('Current URL')).toHaveTextContent(/^\/config\?tab=environment$/)
  })

  it('should fall back to the configuration tab for an unknown URL value', async () => {
    renderPage('/config?tab=unknown')

    expect(screen.getByRole('tab', { name: 'PyRIT Configuration', selected: true })).toBeInTheDocument()
    expect(await screen.findByLabelText('Configuration YAML')).toBeInTheDocument()
  })

  it('should keep configuration edits when tab navigation is cancelled and discard them when confirmed', async () => {
    const user = userEvent.setup()
    renderPage()

    const editor = await screen.findByLabelText('Configuration YAML')
    await user.clear(editor)
    await user.type(editor, 'operator: unsaved\n')
    await user.click(screen.getByRole('tab', { name: 'Environment & Secrets' }))

    expect(screen.getByRole('dialog', { name: 'Discard unsaved changes?' })).toBeInTheDocument()
    expect(screen.getByLabelText('Current URL')).toHaveTextContent(/^\/config$/)
    await user.click(screen.getByRole('button', { name: 'Keep editing' }))
    expect(screen.getByLabelText('Configuration YAML')).toHaveValue('operator: unsaved\n')

    await user.click(screen.getByRole('tab', { name: 'Environment & Secrets' }))
    await user.click(screen.getByRole('button', { name: 'Discard changes' }))
    expect(await screen.findByLabelText('Environment file contents')).toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: 'PyRIT Configuration' }))
    expect(await screen.findByLabelText('Configuration YAML')).toHaveValue('operator: alice\n')
  })

  it('should guard environment edits when changing configuration tabs', async () => {
    const user = userEvent.setup()
    renderPage('/config?tab=environment')

    const editor = await screen.findByLabelText('Environment file contents')
    await user.clear(editor)
    await user.type(editor, 'API_KEY=unsaved\n')
    await user.click(screen.getByRole('tab', { name: 'Initializers' }))

    expect(screen.getByRole('dialog', { name: 'Discard unsaved changes?' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Keep editing' }))
    expect(screen.getByLabelText('Environment file contents')).toHaveValue('API_KEY=unsaved\n')

    await user.click(screen.getByRole('tab', { name: 'Initializers' }))
    await user.click(screen.getByRole('button', { name: 'Discard changes' }))
    expect(await screen.findByTestId('configured-initializer-row-0')).toBeInTheDocument()
  })

  it('should guard application navigation and browser history', async () => {
    const user = userEvent.setup()
    renderPage('/config', ['/'])

    const editor = await screen.findByLabelText('Configuration YAML')
    await user.type(editor, '# unsaved')
    await user.click(screen.getByRole('button', { name: 'Go to scanner' }))

    expect(screen.getByRole('dialog', { name: 'Discard unsaved changes?' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Keep editing' }))
    expect(screen.getByLabelText('Current URL')).toHaveTextContent(/^\/config$/)

    await user.click(screen.getByRole('button', { name: 'Go back' }))
    expect(screen.getByRole('dialog', { name: 'Discard unsaved changes?' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Discard changes' }))
    expect(await screen.findByRole('heading', { name: 'Other page' })).toBeInTheDocument()
    expect(screen.getByLabelText('Current URL')).toHaveTextContent(/^\/$/)
  })

  it('should request browser confirmation before unloading unsaved changes', async () => {
    const user = userEvent.setup()
    renderPage()

    const editor = await screen.findByLabelText('Configuration YAML')
    await user.type(editor, '# unsaved')
    const event = new Event('beforeunload', { cancelable: true })

    expect(window.dispatchEvent(event)).toBe(false)
    expect(event.defaultPrevented).toBe(true)
  })

  it('should guard reloading unsaved configuration content', async () => {
    const user = userEvent.setup()
    renderPage()

    const editor = await screen.findByLabelText('Configuration YAML')
    await user.type(editor, '# unsaved')
    await user.click(screen.getByRole('button', { name: 'Reload' }))

    expect(screen.getByRole('dialog', { name: 'Discard unsaved changes?' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Keep editing' }))
    expect(screen.getByLabelText('Configuration YAML')).toHaveValue('operator: alice\n# unsaved')
    expect(mockedConfigurationApi.getContent).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: 'Reload' }))
    await user.click(screen.getByRole('button', { name: 'Discard changes' }))
    await waitFor(() => expect(mockedConfigurationApi.getContent).toHaveBeenCalledTimes(2))
    expect(await screen.findByLabelText('Configuration YAML')).toHaveValue('operator: alice\n')
  })

  it('should guard reloading an unsaved environment file', async () => {
    const user = userEvent.setup()
    renderPage('/config?tab=environment')

    const editor = await screen.findByLabelText('Environment file contents')
    await user.type(editor, '# unsaved')
    await user.click(screen.getByRole('button', { name: 'Reload' }))

    expect(screen.getByRole('dialog', { name: 'Discard unsaved changes?' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Keep editing' }))
    expect(screen.getByLabelText('Environment file contents')).toHaveValue('API_KEY=value\n# unsaved')
    expect(mockedConfigurationApi.listEnvironmentFiles).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: 'Reload' }))
    await user.click(screen.getByRole('button', { name: 'Discard changes' }))
    await waitFor(() => expect(mockedConfigurationApi.listEnvironmentFiles).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.getByLabelText('Environment file contents')).toHaveValue('API_KEY=value\n'))
  })

})
