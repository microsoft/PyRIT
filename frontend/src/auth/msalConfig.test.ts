// msalConfig.ts imports { LogLevel } from @azure/msal-browser — mock it
// so we don't need the real MSAL SDK in the test environment.
jest.mock("@azure/msal-browser", () => ({
  LogLevel: { Warning: 3 },
}));

import { buildMsalConfig, getGraphScopes, buildLoginRequest } from "./msalConfig";

describe("msalConfig", () => {
  describe("getGraphScopes", () => {
    it("returns the delegated Graph scope", () => {
      expect(getGraphScopes()).toEqual(["User.Read"]);
    });
  });

  describe("buildLoginRequest", () => {
    it("builds request with Graph scopes", () => {
      expect(buildLoginRequest()).toEqual({
        scopes: ["User.Read"],
      });
    });
  });

  // Test 5: buildMsalConfig — assembles MSAL Configuration from AuthConfig
  describe("buildMsalConfig", () => {
    it("builds correct MSAL configuration", () => {
      const authConfig = {
        clientId: "test-client",
        tenantId: "test-tenant",
        allowedGroupIds: "group-1",
      };
      const result = buildMsalConfig(authConfig);

      expect(result.auth.clientId).toBe("test-client");
      expect(result.auth.authority).toBe(
        "https://login.microsoftonline.com/test-tenant"
      );
      expect(result.auth.redirectUri).toBe(window.location.origin);
      expect(result.auth.postLogoutRedirectUri).toBe(window.location.origin);
      expect(result.cache?.cacheLocation).toBe("sessionStorage");
      expect(result.system?.loggerOptions?.piiLoggingEnabled).toBe(false);
    });
  });

  // Tests 6-9: fetchAuthConfig — module-level _cachedConfig state.
  // jest.resetModules() + dynamic import() gives each test a fresh module.
  describe("fetchAuthConfig", () => {
    const originalFetch = global.fetch;

    beforeEach(() => {
      jest.resetModules();
      jest.doMock("@azure/msal-browser", () => ({
        LogLevel: { Warning: 3 },
      }));
      global.fetch = jest.fn();
    });

    afterEach(() => {
      global.fetch = originalFetch;
    });

    it("fetches config from /api/auth/config", async () => {
      const mockConfig = { clientId: "abc", tenantId: "xyz", allowedGroupIds: "g1" };
      (global.fetch as jest.Mock).mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(mockConfig),
      });

      const { fetchAuthConfig } = await import("./msalConfig");
      const result = await fetchAuthConfig();

      expect(result).toEqual(mockConfig);
      expect(global.fetch).toHaveBeenCalledWith("/api/auth/config");
    });

    it("throws when response is not ok (transient failure is not auth-disabled)", async () => {
      (global.fetch as jest.Mock).mockResolvedValue({
        ok: false,
        status: 503,
        statusText: "Service Unavailable",
      });

      const { fetchAuthConfig } = await import("./msalConfig");

      await expect(fetchAuthConfig()).rejects.toThrow("/api/auth/config returned 503 Service Unavailable");
    });

    it("throws on network error (transient failure is not auth-disabled)", async () => {
      const networkError = new Error("Network error");
      (global.fetch as jest.Mock).mockRejectedValue(networkError);

      const { fetchAuthConfig } = await import("./msalConfig");

      await expect(fetchAuthConfig()).rejects.toMatchObject({
        message: "Failed to reach /api/auth/config: Network error",
        cause: networkError,
      });
    });
  });
});
