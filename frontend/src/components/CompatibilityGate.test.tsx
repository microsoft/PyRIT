import { StrictMode, useEffect, useState, type ReactNode } from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { AxiosError, AxiosHeaders, type InternalAxiosRequestConfig } from 'axios'
import { AuthProvider } from '../auth/AuthProvider'
import * as msalConfig from '../auth/msalConfig'
import { apiClient, authApi, setMsalInstance, versionApi } from '../services/api'
import * as compatibilityModule from '../services/compatibility'
import { CompatibilityGate } from './CompatibilityGate'

const mockAccount = { username: 'user@example.com' }
let mockAuthenticated = true
const mockToken = jest.fn(async () => ({ accessToken: 'access-token' }))
const mockInitialize = jest.fn(async () => {})
const mockLoginRedirect = jest.fn(async () => {})
const mockInstance = {
  initialize: mockInitialize,
  handleRedirectPromise: jest.fn(async () => null),
  getActiveAccount: jest.fn(() => mockAuthenticated ? mockAccount : null),
  getAllAccounts: jest.fn(() => mockAuthenticated ? [mockAccount] : []),
  setActiveAccount: jest.fn(),
  acquireTokenSilent: mockToken,
  loginRedirect: mockLoginRedirect,
}

jest.mock('@azure/msal-browser', () => ({
  ...jest.requireActual('@azure/msal-browser'),
  PublicClientApplication: jest.fn(() => mockInstance),
}))

jest.mock('@azure/msal-react', () => ({
  MsalProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
  AuthenticatedTemplate: ({ children }: { children: ReactNode }) => mockAuthenticated ? <>{children}</> : null,
  UnauthenticatedTemplate: ({ children }: { children: ReactNode }) => mockAuthenticated ? null : <>{children}</>,
  useMsal: () => ({ instance: mockInstance }),
}))

const bundledId = `1.2.0.dev0+g${'a'.repeat(40)}`
const backendId = `1.2.0.dev0+g${'b'.repeat(40)}`
const adapter = jest.fn()
const originalAdapter = apiClient.defaults.adapter

function response(config: InternalAxiosRequestConfig, data: unknown, status = 200) {
  return { config, data, status, statusText: String(status), headers: new AxiosHeaders() }
}

function problem(config: InternalAxiosRequestConfig, status: number, type: string) {
  return new AxiosError('Backend rejected request', undefined, config, undefined,
    response(config, { type, expected: backendId, actual: bundledId }, status))
}

function BusinessUI({ unmount = () => {} }: { unmount?: () => void }) {
  const [draft, setDraft] = useState('')
  useEffect(() => {
    void authApi.getAccess().catch(() => {})
    return unmount
  }, [unmount])
  return <input aria-label="Unsaved draft" value={draft} onChange={(event) => setDraft(event.target.value)} />
}

function application(children: ReactNode = <BusinessUI />) {
  return <AuthProvider><CompatibilityGate>{children}</CompatibilityGate></AuthProvider>
}

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () { this.open = true }
})

afterAll(() => {
  Reflect.deleteProperty(HTMLDialogElement.prototype, 'showModal')
})

beforeEach(() => {
  jest.clearAllMocks()
  mockAuthenticated = true
  mockInitialize.mockResolvedValue(undefined)
  mockToken.mockResolvedValue({ accessToken: 'access-token' })
  setMsalInstance(null)
  jest.replaceProperty(compatibilityModule, 'compatibility', new compatibilityModule.CompatibilityStore(bundledId))
  jest.spyOn(msalConfig, 'fetchAuthConfig').mockResolvedValue({ clientId: 'client', tenantId: 'tenant', allowedGroupIds: '' })
  jest.spyOn(console, 'error').mockImplementation(() => {})
  apiClient.defaults.adapter = adapter
  adapter.mockReset().mockImplementation(async (config: InternalAxiosRequestConfig) =>
    response(config, config.url === '/version' ? { compatibility_id: bundledId } : { isAdmin: true }))
})

afterEach(() => {
  cleanup()
  apiClient.defaults.adapter = originalAdapter
  jest.restoreAllMocks()
})

it('waits for auth initialization, authenticates version, then stamps auth/access', async () => {
  let finishAuth!: () => void
  mockInitialize.mockImplementationOnce(() => new Promise((resolve) => { finishAuth = resolve }))
  let finishVersion!: () => void
  adapter.mockImplementationOnce((config: InternalAxiosRequestConfig) => new Promise((resolve) => {
    finishVersion = () => resolve(response(config, { compatibility_id: bundledId }))
  }))
  render(application())
  await waitFor(() => expect(mockInitialize).toHaveBeenCalled())
  expect(adapter).not.toHaveBeenCalled()
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
  await act(async () => { finishAuth() })
  await waitFor(() => expect(adapter).toHaveBeenCalledTimes(1))
  expect(adapter.mock.calls[0][0].url).toBe('/version')
  expect(adapter.mock.calls[0][0].headers.get('Authorization')).toBe('Bearer access-token')
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
  await act(async () => { finishVersion() })
  await screen.findByRole('textbox')
  await waitFor(() => expect(adapter).toHaveBeenCalledTimes(2))
  expect(adapter.mock.calls[1][0].url).toBe('/auth/access')
  expect(adapter.mock.calls[1][0].headers.get('PyRIT-Compatibility-ID')).toBe(bundledId)
  expect(adapter.mock.calls[1][0].headers.get('Authorization')).toBe('Bearer access-token')
})

it('does not handshake while the user is unauthenticated', async () => {
  mockAuthenticated = false
  render(application())
  await screen.findByText('Redirecting to login...')
  expect(mockLoginRedirect).toHaveBeenCalledTimes(1)
  expect(adapter).not.toHaveBeenCalled()
})

it('still requires compatibility when local authentication is disabled', async () => {
  jest.mocked(msalConfig.fetchAuthConfig).mockResolvedValue({ clientId: '', tenantId: '', allowedGroupIds: '' })
  render(application())
  await screen.findByRole('textbox')
  expect(adapter.mock.calls[0][0].url).toBe('/version')
  expect(adapter.mock.calls[1][0].url).toBe('/auth/access')
  expect(adapter.mock.calls[0][0].headers.get('Authorization')).toBeUndefined()
})

it.each([{}, { compatibility_id: null }, { compatibility_id: '1.2.0+gabc' }, { compatibility_id: backendId }])(
  'blocks startup without mounting business UI for %p', async (version) => {
    adapter.mockImplementation(async (config: InternalAxiosRequestConfig) => response(config, version))
    render(application())
    const notice = await screen.findByRole('alertdialog')
    expect(notice).toHaveAttribute('open')
    expect(fireEvent(notice, new Event('cancel', { cancelable: true }))).toBe(false)
    expect(notice).toHaveAttribute('open')
    expect(screen.queryByRole('textbox', { hidden: true })).not.toBeInTheDocument()
    expect(adapter).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Reload page' })).toBeVisible()
    await expect(authApi.getAccess()).rejects.toThrow('compatibility')
    expect(adapter).toHaveBeenCalledTimes(1)
  },
)

it('coalesces the startup handshake under StrictMode', async () => {
  render(<StrictMode>{application()}</StrictMode>)
  await screen.findByRole('textbox')
  expect(adapter.mock.calls.filter(([config]) => config.url === '/version')).toHaveLength(1)
})

it('preserves authenticated 401 refresh during the version handshake', async () => {
  adapter.mockImplementationOnce(async (config: InternalAxiosRequestConfig) => {
    throw problem(config, 401, 'urn:pyrit:authentication:required')
  })
  render(application())
  await screen.findByRole('textbox')
  expect(adapter.mock.calls.map(([config]) => config.url)).toEqual(['/version', '/version', '/auth/access'])
  expect(mockToken).toHaveBeenCalledWith(expect.objectContaining({ forceRefresh: true }))
  expect(adapter.mock.calls[1][0].headers.get('Authorization')).toBe('Bearer access-token')
})

it('blocks startup after authentication still fails following refresh', async () => {
  adapter.mockImplementation(async (config: InternalAxiosRequestConfig) => {
    throw problem(config, 401, 'urn:pyrit:authentication:required')
  })
  render(application())
  await screen.findByRole('alertdialog')
  expect(adapter.mock.calls.map(([config]) => config.url)).toEqual(['/version', '/version'])
  expect(screen.queryByRole('textbox', { hidden: true })).not.toBeInTheDocument()
})

it.each([
  [400, 'urn:pyrit:compatibility:invalid'],
  [409, 'urn:pyrit:compatibility:mismatch'],
] as const)('blocks after backend problem %i without unmounting or replaying', async (status, type) => {
  const unmount = jest.fn()
  render(application(<BusinessUI unmount={unmount} />))
  const input = await screen.findByRole('textbox')
  fireEvent.change(input, { target: { value: 'retain my draft' } })
  await waitFor(() => expect(adapter).toHaveBeenCalledTimes(2))
  adapter.mockImplementationOnce(async (config: InternalAxiosRequestConfig) => { throw problem(config, status, type) })
  await act(async () => {
    await expect(apiClient.post('/attacks', { mutation: true })).rejects.toBeInstanceOf(AxiosError)
  })
  expect(screen.getByRole('alertdialog')).toHaveTextContent('No failed operation will be replayed')
  expect(screen.getByRole('alertdialog')).toHaveTextContent(backendId)
  expect(input).toHaveValue('retain my draft')
  expect(input).not.toBeVisible()
  expect(unmount).not.toHaveBeenCalled()
  expect(adapter).toHaveBeenCalledTimes(3)
  await expect(apiClient.post('/attacks', { mutation: true })).rejects.toThrow('compatibility')
  await expect(authApi.getAccess()).rejects.toThrow('compatibility')
  expect(adapter).toHaveBeenCalledTimes(3)
  await versionApi.getVersion()
  expect(compatibilityModule.compatibility.getSnapshot().status).toBe('blocked')
  expect(unmount).not.toHaveBeenCalled()
})

it.each(['/health', '/auth/config', '/version', '/media', '/media?filename=file.png', 'http://localhost:8000/api/health?check=1', 'http://localhost:8000/api/version'])(
  'allows neutral request %s while blocked', async (url) => {
    compatibilityModule.compatibility.block('Blocked')
    await apiClient.get(url)
    expect(adapter).toHaveBeenCalledTimes(1)
    expect(adapter.mock.calls[0][0].headers.get('PyRIT-Compatibility-ID')).toBeUndefined()
  },
)

it.each(['/auth/access', '/auth/configuration', '/version/extra', '/media/file.png', '/media-other', '/attacks', '/api/auth/access', '/api/version'])(
  'blocks business request %s before handshake', async (url) => {
    await expect(apiClient.get(url)).rejects.toThrow('compatibility')
    expect(adapter).not.toHaveBeenCalled()
  },
)

it('overwrites caller-supplied stamps on all business methods', async () => {
  await compatibilityModule.compatibility.verify(async () => ({ compatibility_id: bundledId }))
  for (const method of ['get', 'post', 'put', 'patch', 'delete']) {
    await apiClient.request({ url: '/attacks', method, headers: { 'PyRIT-Compatibility-ID': 'wrong' } })
  }
  expect(adapter).toHaveBeenCalledTimes(5)
  for (const [config] of adapter.mock.calls) {
    expect(config.headers.get('PyRIT-Compatibility-ID')).toBe(bundledId)
  }
})

it('rechecks invalidation after asynchronous token acquisition', async () => {
  await compatibilityModule.compatibility.verify(async () => ({ compatibility_id: bundledId }))
  setMsalInstance(mockInstance as unknown as Parameters<typeof setMsalInstance>[0])
  let finishToken!: (token: { accessToken: string }) => void
  mockToken.mockImplementationOnce(() => new Promise((resolve) => { finishToken = resolve }))
  const pendingRequest = apiClient.post('/attacks', {})
  const rejected = expect(pendingRequest).rejects.toThrow('compatibility')
  await waitFor(() => expect(mockToken).toHaveBeenCalled())
  compatibilityModule.compatibility.block('Deployment changed')
  finishToken({ accessToken: 'access-token' })
  await rejected
  expect(adapter).not.toHaveBeenCalled()
})

it('does not invalidate on an unrelated business conflict', async () => {
  await compatibilityModule.compatibility.verify(async () => ({ compatibility_id: bundledId }))
  adapter.mockImplementationOnce(async (config: InternalAxiosRequestConfig) => {
    throw problem(config, 409, 'urn:pyrit:business:conflict')
  })
  await expect(apiClient.post('/attacks', {})).rejects.toBeInstanceOf(AxiosError)
  expect(compatibilityModule.compatibility.getSnapshot().status).toBe('ready')
  await apiClient.get('/attacks')
  expect(adapter).toHaveBeenCalledTimes(2)
})
