import { render, screen, waitFor } from "@testing-library/react";

// ---------------------------------------------------------------------------
// Mock setup — declare mock fns before jest.mock so they're hoisted correctly
// ---------------------------------------------------------------------------

const mockFetchAuthConfig = jest.fn();
const mockBuildMsalConfig = jest.fn().mockReturnValue({
  auth: {
    clientId: "test-client",
    authority: "https://login.microsoftonline.com/test-tenant",
    redirectUri: "http://localhost",
    postLogoutRedirectUri: "http://localhost",
  },
  cache: { cacheLocation: "sessionStorage" },
  system: { loggerOptions: { logLevel: 3, piiLoggingEnabled: false } },
});
const mockBuildLoginRequest = jest.fn().mockReturnValue({
  scopes: ["test-client/access"],
});

jest.mock("./msalConfig", () => ({
  fetchAuthConfig: (...args: unknown[]) => mockFetchAuthConfig(...args),
  buildMsalConfig: (...args: unknown[]) => mockBuildMsalConfig(...args),
  buildLoginRequest: (...args: unknown[]) => mockBuildLoginRequest(...args),
}));

const mockSetMsalInstance = jest.fn();
const mockSetClientId = jest.fn();

jest.mock("../services/api", () => ({
  setMsalInstance: (...args: unknown[]) => mockSetMsalInstance(...args),
  setClientId: (...args: unknown[]) => mockSetClientId(...args),
}));

// MSAL browser mocks — PublicClientApplication instance methods
const mockInitialize = jest.fn().mockResolvedValue(undefined);
const mockHandleRedirectPromise = jest.fn().mockResolvedValue(null);
const mockGetActiveAccount = jest.fn().mockReturnValue(null);
const mockGetAllAccounts = jest.fn().mockReturnValue([]);
const mockSetActiveAccount = jest.fn();
const mockAddEventCallback = jest.fn();
const mockLoginRedirect = jest.fn().mockResolvedValue(undefined);

jest.mock("@azure/msal-browser", () => ({
  PublicClientApplication: jest.fn().mockImplementation(() => ({
    initialize: mockInitialize,
    handleRedirectPromise: mockHandleRedirectPromise,
    getActiveAccount: mockGetActiveAccount,
    getAllAccounts: mockGetAllAccounts,
    setActiveAccount: mockSetActiveAccount,
    addEventCallback: mockAddEventCallback,
    loginRedirect: mockLoginRedirect,
  })),
  EventType: { LOGIN_SUCCESS: "msal:loginSuccess" },
  InteractionRequiredAuthError: class extends Error {},
}));

// MSAL React mocks — MsalProvider renders children, templates render children
// unconditionally so we can test both authenticated and unauthenticated paths.
jest.mock("@azure/msal-react", () => ({
  MsalProvider: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="msal-provider">{children}</div>
  ),
  AuthenticatedTemplate: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="authenticated">{children}</div>
  ),
  UnauthenticatedTemplate: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="unauthenticated">{children}</div>
  ),
  useMsal: () => ({
    instance: {
      loginRedirect: mockLoginRedirect,
      getActiveAccount: mockGetActiveAccount,
      getAllAccounts: mockGetAllAccounts,
    },
  }),
}));

import { AuthProvider } from "./AuthProvider";

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("AuthProvider", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockGetActiveAccount.mockReturnValue(null);
    mockGetAllAccounts.mockReturnValue([]);
    mockHandleRedirectPromise.mockResolvedValue(null);
  });
  afterEach(() => {
    jest.restoreAllMocks();
  });

  // Test 10: fetchAuthConfig never resolves → stuck in loading state
  it("shows loading state while initializing", () => {
    mockFetchAuthConfig.mockReturnValue(new Promise(() => {}));

    render(
      <AuthProvider>
        <div>Child</div>
      </AuthProvider>
    );

    expect(screen.getByText("Initializing authentication...")).toBeVisible();
  });

  // Test 11: empty clientId + tenantId → auth disabled, children render directly
  it("renders children directly when auth is disabled", async () => {
    mockFetchAuthConfig.mockResolvedValue({
      clientId: "",
      tenantId: "",
      allowedGroupIds: "",
    });

    render(
      <AuthProvider>
        <div data-testid="child">Hello</div>
      </AuthProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId("child")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("msal-provider")).not.toBeInTheDocument();
  });

  // Test 12: clientId present but tenantId missing → still disabled (OR branch)
  it("renders children when only tenantId is missing", async () => {
    mockFetchAuthConfig.mockResolvedValue({
      clientId: "some-client",
      tenantId: "",
      allowedGroupIds: "",
    });

    render(
      <AuthProvider>
        <div data-testid="child">Hello</div>
      </AuthProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId("child")).toBeInTheDocument();
    });
  });

  // Test 13: fetchAuthConfig rejects with Error → err.message shown
  it("shows error when initialization fails with Error", async () => {
    mockFetchAuthConfig.mockRejectedValue(new Error("Config fetch failed"));

    render(
      <AuthProvider>
        <div>Child</div>
      </AuthProvider>
    );

    await waitFor(() => {
      expect(screen.getByText("Authentication Error")).toBeVisible();
      expect(screen.getByText("Config fetch failed")).toBeVisible();
    });
  });

  // Test 14: fetchAuthConfig rejects with non-Error → generic message
  it("shows generic error when initialization fails with non-Error", async () => {
    mockFetchAuthConfig.mockRejectedValue("string error");

    render(
      <AuthProvider>
        <div>Child</div>
      </AuthProvider>
    );

    await waitFor(() => {
      expect(screen.getByText("Authentication Error")).toBeVisible();
      expect(
        screen.getByText("Failed to initialize authentication")
      ).toBeVisible();
    });
  });

  // Test 15: full happy path → MSAL initialized, MsalProvider renders
  it("initializes MSAL and renders MsalProvider when auth is enabled", async () => {
    mockFetchAuthConfig.mockResolvedValue({
      clientId: "test-client",
      tenantId: "test-tenant",
      allowedGroupIds: "g1",
    });

    render(
      <AuthProvider>
        <div data-testid="child">Hello</div>
      </AuthProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId("msal-provider")).toBeInTheDocument();
    });
    expect(mockInitialize).toHaveBeenCalled();
    expect(mockHandleRedirectPromise).toHaveBeenCalled();
    expect(mockSetMsalInstance).toHaveBeenCalled();
    expect(mockSetClientId).toHaveBeenCalledWith("test-client");
  });

  // Test 16: handleRedirectPromise returns account → setActiveAccount called
  it("sets active account from redirect result", async () => {
    const mockAccount = { username: "user@test.com" };
    mockFetchAuthConfig.mockResolvedValue({
      clientId: "test-client",
      tenantId: "test-tenant",
      allowedGroupIds: "g1",
    });
    mockHandleRedirectPromise.mockResolvedValue({ account: mockAccount });

    render(
      <AuthProvider>
        <div>Child</div>
      </AuthProvider>
    );

    await waitFor(() => {
      expect(mockSetActiveAccount).toHaveBeenCalledWith(mockAccount);
    });
  });

  // Test 17: no redirect, no active account, but cached account exists
  it("falls back to cached account when no redirect result", async () => {
    const cachedAccount = { username: "cached@test.com" };
    mockFetchAuthConfig.mockResolvedValue({
      clientId: "test-client",
      tenantId: "test-tenant",
      allowedGroupIds: "g1",
    });
    mockGetActiveAccount.mockReturnValue(null);
    mockGetAllAccounts.mockReturnValue([cachedAccount]);

    render(
      <AuthProvider>
        <div>Child</div>
      </AuthProvider>
    );

    await waitFor(() => {
      expect(mockSetActiveAccount).toHaveBeenCalledWith(cachedAccount);
    });
  });

  // Test 18: UnauthenticatedTemplate renders LoginRedirect text
  it("renders LoginRedirect in unauthenticated template", async () => {
    mockFetchAuthConfig.mockResolvedValue({
      clientId: "test-client",
      tenantId: "test-tenant",
      allowedGroupIds: "g1",
    });

    render(
      <AuthProvider>
        <div>Child</div>
      </AuthProvider>
    );

    await waitFor(() => {
      expect(screen.getByText("Redirecting to login...")).toBeVisible();
    });
  });

  // Test 19: loginRedirect rejects → .catch logs the error
  it("handles login redirect failure gracefully", async () => {
      const consoleSpy = jest.spyOn(console, "error").mockImplementation(() => {});
      mockFetchAuthConfig.mockResolvedValue({
        clientId: "test-client",
        tenantId: "test-tenant",
        allowedGroupIds: "g1",
      });
      mockLoginRedirect.mockRejectedValue(new Error("Redirect failed"));

      render(
        <AuthProvider>
          <div>Child</div>
        </AuthProvider>
      );

      await waitFor(() => {
        expect(consoleSpy).toHaveBeenCalledWith(
          "Login redirect failed:",
          expect.any(Error)
        );
      });
    });

    it("does not crash when a child calls useMsal and auth is disabled", async () => {
      const {useMsal} = await import("@azure/msal-react");

      function MsalConsumer() {
        const { instance: msalInstance } = useMsal();
        return <div data-testid="consumer">Got instance: {String(msalInstance)}</div>;
      }

      mockFetchAuthConfig.mockResolvedValue({
        clientId: "",
        tenantId: "",
        allowedGroupIds: "",
      });

      render(
        <AuthProvider>
          <MsalConsumer />
        </AuthProvider>
      );

      await waitFor(() => {
        expect(screen.getByTestId("consumer")).toBeVisible();
      });
    });
});
