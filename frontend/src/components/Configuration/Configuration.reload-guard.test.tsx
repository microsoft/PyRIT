import type { ReactElement } from 'react'

import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider, useLocation, useNavigate } from 'react-router'

import { configurationApi } from '@/services/api'
import { mockJsdomLayout } from '@/test-utils/mockJsdomLayout'

import Configuration from './Configuration'

mockJsdomLayout()

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

function RouterProbe(): ReactElement {
  const location = useLocation()
  const navigate = useNavigate()

  return (
    <>
      <output aria-label="Current URL">{location.pathname}{location.search}</output>
      <button type="button" onClick={() => void navigate('/scanner')}>Go to scanner</button>
    </>
  )
}

function renderPage(): void {
  const router = createMemoryRouter([
    {
      path: '/config',
      element: (
        <>
          <Configuration />
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
    initialEntries: ['/config?tab=environment'],
  })

  render(
    <FluentProvider theme={webLightTheme}>
      <RouterProvider router={router} />
    </FluentProvider>,
  )
}

describe('Configuration failed environment reload guard', () => {
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
    mockedConfigurationApi.listEnvironmentFiles.mockResolvedValueOnce({
      items: [
        {
          id: '0',
          name: '.env',
          path: 'C:/Users/test/.pyrit/.env',
          content: '',
          exists: true,
          version: 'v1',
        },
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
  })

  it('keeps navigation protection when a confirmed reload fails and preserves drafts', async () => {
    const user = userEvent.setup()
    renderPage()

    const editor = await screen.findByLabelText('Environment file contents')
    await user.clear(editor)
    await user.type(editor, 'API_KEY=unsaved\n')

    mockedConfigurationApi.listEnvironmentFiles.mockRejectedValueOnce(new Error('Reload failed'))

    await user.click(screen.getByRole('button', { name: 'Reload' }))
    await user.click(await screen.findByRole('button', { name: 'Discard changes' }))

    expect(await screen.findByText('Reload failed')).toBeInTheDocument()
    expect(screen.getByLabelText('Environment file contents')).toHaveValue('API_KEY=unsaved\n')

    await user.click(await screen.findByRole('button', { name: 'Go to scanner' }))

    expect(await screen.findByRole('dialog', { name: 'Discard unsaved changes?' })).toBeInTheDocument()
    expect(screen.getByLabelText('Current URL')).toHaveTextContent(/^\/config\?tab=environment$/)
  })
})
