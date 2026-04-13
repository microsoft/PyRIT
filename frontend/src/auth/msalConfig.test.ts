// msalConfig.ts imports { LogLevel } from @azure/msal-browser — mock it
// so we don't need the real MSAL SDK in the test environment.
jest.mock("@azure/msal-browser", () => ({
  LogLevel: { Warning: 3 },
}));

import { buildMsalConfig, getApiScopes, buildLoginRequest } from "./msalConfig";

describe("msalConfig", () => {
  // Tests 1-2: getApiScopes — two branches on the !clientId check (line 75)
  describe("getApiScopes", () => {
    it("returns default scopes when clientId is empty", () => {
      expect(getApiScopes("")).toEqual(["openid", "profile", "email"]);
    });

    it("returns client-specific scope when clientId is provided", () => {
      expect(getApiScopes("my-client-id")).toEqual(["api://my-client-id/access"]);
    });
  });

  // Tests 3-4: buildLoginRequest — wraps getApiScopes in { scopes }
  describe("buildLoginRequest", () => {
    it("builds request with client-specific scopes", () => {
      expect(buildLoginRequest("my-client-id")).toEqual({
        scopes: ["api://my-client-id/access"],
      });
    });

    it("builds request with default scopes when clientId is empty", () => {
      expect(buildLoginRequest("")).toEqual({
        scopes: ["openid", "profile", "email"],
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

    it("returns empty config when response is not ok", async () => {
      (global.fetch as jest.Mock).mockResolvedValue({ ok: false });

      const { fetchAuthConfig } = await import("./msalConfig");
      const result = await fetchAuthConfig();

      expect(result).toEqual({ clientId: "", tenantId: "", allowedGroupIds: "" });
    });

    it("returns empty config on network error", async () => {
      (global.fetch as jest.Mock).mockRejectedValue(new Error("Network error"));

      const { fetchAuthConfig } = await import("./msalConfig");
      const result = await fetchAuthConfig();

      expect(result).toEqual({ clientId: "", tenantId: "", allowedGroupIds: "" });
    });
  });
});
