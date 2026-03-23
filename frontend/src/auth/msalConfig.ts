// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * MSAL configuration for Entra ID PKCE authentication.
 *
 * The client ID and tenant ID are injected at runtime via the /api/auth/config
 * endpoint (served by the backend from environment variables). This avoids
 * hardcoding tenant-specific values in the frontend bundle.
 *
 * Uses access tokens (not ID tokens) with an API-specific scope so that
 * Entra ID includes the `groups` claim for group-based authorization.
 */

import { type Configuration, LogLevel } from '@azure/msal-browser'

export interface AuthConfig {
  clientId: string
  tenantId: string
  allowedGroupId: string
}

let _cachedConfig: AuthConfig | null = null

export async function fetchAuthConfig(): Promise<AuthConfig> {
  if (_cachedConfig) return _cachedConfig

  try {
    const response = await fetch('/api/auth/config')
    if (!response.ok) {
      // Auth endpoint not available — treat as auth disabled
      return { clientId: '', tenantId: '', allowedGroupId: '' }
    }
    _cachedConfig = (await response.json()) as AuthConfig
    return _cachedConfig
  } catch {
    // Network error (e.g., backend not running yet) — treat as auth disabled
    return { clientId: '', tenantId: '', allowedGroupId: '' }
  }
}

export function buildMsalConfig(authConfig: AuthConfig): Configuration {
  return {
    auth: {
      clientId: authConfig.clientId,
      authority: `https://login.microsoftonline.com/${authConfig.tenantId}`,
      redirectUri: window.location.origin,
      postLogoutRedirectUri: window.location.origin,
    },
    cache: {
      cacheLocation: 'sessionStorage',
    },
    system: {
      loggerOptions: {
        logLevel: LogLevel.Warning,
        piiLoggingEnabled: false,
      },
    },
  }
}

/**
 * Build the API scopes for token acquisition.
 *
 * Requests the explicit `access` scope rather than `.default` to avoid
 * triggering admin consent in corporate tenants. The `.default` scope requires
 * the app to list itself in `requiredResourceAccess`, which triggers mandatory
 * admin consent in the Microsoft tenant. Using the explicit scope bypasses this.
 *
 * The `access` scope is defined in the app registration's "Expose an API"
 * configuration. Access tokens include the `groups` claim when the app manifest
 * has `groupMembershipClaims: "SecurityGroup"` configured.
 */
export function getApiScopes(clientId: string): string[] {
  if (!clientId) return ['openid', 'profile', 'email']
  return [`${clientId}/access`]
}

export function buildLoginRequest(clientId: string) {
  return {
    scopes: getApiScopes(clientId),
  }
}
