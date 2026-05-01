import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { UserAccountButton } from './UserAccountButton'

const mockLoginRedirect = jest.fn()
const mockLogoutRedirect = jest.fn()
const mockGetActiveAccount = jest.fn()
let mockAccounts: { name?: string; username?: string }[] = []

jest.mock('@azure/msal-react', () => ({
  useMsal: () => ({
    instance: {
      getActiveAccount: mockGetActiveAccount,
      loginRedirect: mockLoginRedirect,
      logoutRedirect: mockLogoutRedirect,
    },
    accounts: mockAccounts,
  }),
}))

let mockAuthConfig = { clientId: '', tenantId: '', allowedGroupIds: '' }

jest.mock('../auth/AuthConfigContext', () => ({
  useAuthConfig: () => mockAuthConfig,
}))

jest.mock('../auth/msalConfig', () => ({
  buildLoginRequest: (clientId: string) => ({ scopes: [`${clientId}/.default`] }),
}))

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

describe('UserAccountButton', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockGetActiveAccount.mockReturnValue(null)
    mockLoginRedirect.mockResolvedValue(undefined)
    mockLogoutRedirect.mockResolvedValue(undefined)
    mockAuthConfig = { clientId: '', tenantId: '', allowedGroupIds: '' }
    mockAccounts = []
  })

  it('returns null when auth is disabled (no clientId)', () => {
    render(
      <TestWrapper>
        <UserAccountButton />
      </TestWrapper>
    )

    // UserAccountButton renders null — no buttons appear
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('renders Log In button when auth enabled but no account', () => {
    mockAuthConfig = { clientId: 'test-client-id', tenantId: 'test-tenant', allowedGroupIds: '' }

    render(
      <TestWrapper>
        <UserAccountButton />
      </TestWrapper>
    )

    expect(screen.getByRole('button', { name: /log in/i })).toBeInTheDocument()
  })

  it('calls loginRedirect when Log In is clicked', async () => {
    mockAuthConfig = { clientId: 'test-client-id', tenantId: 'test-tenant', allowedGroupIds: '' }
    const user = userEvent.setup()

    render(
      <TestWrapper>
        <UserAccountButton />
      </TestWrapper>
    )

    await user.click(screen.getByRole('button', { name: /log in/i }))

    expect(mockLoginRedirect).toHaveBeenCalledWith({ scopes: ['test-client-id/.default'] })
  })

  it('renders user display name and Sign Out when account exists', () => {
    mockAuthConfig = { clientId: 'test-client-id', tenantId: 'test-tenant', allowedGroupIds: '' }
    mockGetActiveAccount.mockReturnValue({
      name: 'Alice Smith',
      username: 'alice@example.com',
    })

    render(
      <TestWrapper>
        <UserAccountButton />
      </TestWrapper>
    )

    expect(screen.getByText('Alice Smith')).toBeInTheDocument()
  })

  it('renders user display name when account comes from accounts[0] (no active account)', () => {
    mockAuthConfig = { clientId: 'test-client-id', tenantId: 'test-tenant', allowedGroupIds: '' }
    mockAccounts = [{ name: 'Bob Jones', username: 'bob@example.com' }]

    render(
      <TestWrapper>
        <UserAccountButton />
      </TestWrapper>
    )

    expect(screen.getByText('Bob Jones')).toBeInTheDocument()
  })

  it('calls logoutRedirect when Sign Out is clicked', async () => {
    mockAuthConfig = { clientId: 'test-client-id', tenantId: 'test-tenant', allowedGroupIds: '' }
    mockGetActiveAccount.mockReturnValue({
      name: 'Alice Smith',
      username: 'alice@example.com',
    })
    const user = userEvent.setup()

    render(
      <TestWrapper>
        <UserAccountButton />
      </TestWrapper>
    )

    // Open the popover by clicking the user button
    await user.click(screen.getByRole('button', { name: /alice smith/i }))

    await user.click(screen.getByRole('button', { name: /sign out/i }))

    expect(mockLogoutRedirect).toHaveBeenCalled()
  })
})
