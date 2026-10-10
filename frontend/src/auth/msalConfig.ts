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
  let response: Response
  try {
    response = await fetch('/api/auth/config')
  } catch (error) {
    // A network error (e.g., backend not running yet) is a transient
    // infrastructure failure, not proof that auth is disabled. Surface it so
    // AuthProvider can show its error state instead of rendering the shell
    // while protected APIs return 401.
    const fetchError = new Error(
      `Failed to reach /api/auth/config: ${error instanceof Error ? error.message : String(error)}`,
    )
    // ErrorOptions requires ES2022, while this project targets ES2020.
    Object.defineProperty(fetchError, 'cause', { value: error })
    throw fetchError
  }
  if (!response.ok) {
    // HTTP-level failures on the config endpoint are equally inconclusive.
    throw new Error(`/api/auth/config returned ${response.status} ${response.statusText}`)
  }
  return (await response.json()) as AuthConfig
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
