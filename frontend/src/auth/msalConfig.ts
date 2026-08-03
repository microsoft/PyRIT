// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * MSAL configuration for Entra ID PKCE authentication.
 *
 * The client ID and tenant ID are injected at runtime via the /api/auth/config
 * endpoint (served by the backend from environment variables). This avoids
 * hardcoding tenant-specific values in the frontend bundle.
 *
 * Uses a delegated Microsoft Graph access token. The backend forwards this
 * token only to trusted Graph endpoints to authenticate the user and resolve
 * group membership.
 */

import { type Configuration, LogLevel } from '@azure/msal-browser'

const GRAPH_USER_READ_SCOPE = 'User.Read'

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

/** Build the delegated Microsoft Graph scopes used for authentication. */
export function getGraphScopes(): string[] {
  return [GRAPH_USER_READ_SCOPE]
}

export function buildLoginRequest(): { scopes: string[] } {
  return {
    scopes: getGraphScopes(),
  }
}
