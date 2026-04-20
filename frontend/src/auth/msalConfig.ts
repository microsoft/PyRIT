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
  allowedGroupIds: string
}

export async function fetchAuthConfig(): Promise<AuthConfig> {
  try {
    const response = await fetch('/api/auth/config')
    if (!response.ok) {
      // Auth endpoint not available — treat as auth disabled
      return { clientId: '', tenantId: '', allowedGroupIds: '' }
    }
    return (await response.json()) as AuthConfig
  } catch {
    // Network error (e.g., backend not running yet) — treat as auth disabled
    return { clientId: '', tenantId: '', allowedGroupIds: '' }
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
  * Requests the app's custom `access` scope so the resulting access token has
  * `aud` equal to the app's client ID. The explicit scope name avoids the
  * `.default` shorthand, which resolves via `requiredResourceAccess` and
  * triggers mandatory admin consent in the Microsoft corporate tenant.
  *
  * The `access` scope is defined under "Expose an API" on the app registration.
  */
export function getApiScopes(clientId: string): string[] {
  if (!clientId) return ['openid', 'profile', 'email']
  return [`api://${clientId}/access`]
}

export function buildLoginRequest(clientId: string) {
  return {
    scopes: getApiScopes(clientId),
  }
}
