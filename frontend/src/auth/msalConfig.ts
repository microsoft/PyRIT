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

export async function fetchAuthConfig(): Promise<AuthConfig> {
  try {
    const response = await fetch('/api/auth/config')
    if (!response.ok) {
      // Auth endpoint not available — treat as auth disabled
      return { clientId: '', tenantId: '', allowedGroupId: '' }
    }
    return (await response.json()) as AuthConfig
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
 * Requests the Microsoft Graph `User.Read` delegated scope so the resulting
 * access token has `aud: https://graph.microsoft.com`. This serves two purposes:
 *
 * 1. The backend can forward the same token to Graph for groups-overage
 *    resolution (users in >200 groups). A custom app-audience token would be
 *    rejected by Graph with 401.
 * 2. It avoids needing a custom "Expose an API" scope on the app registration,
 *    simplifying setup and eliminating admin-consent triggers in corporate tenants.
 *
 * The token still contains identity claims (oid, name, groups) because the app
 * manifest has `groupMembershipClaims: "SecurityGroup"` configured.
 */
export function getApiScopes(clientId: string): string[] {
  if (!clientId) return ['openid', 'profile', 'email']
  return ['https://graph.microsoft.com/User.Read']
}

export function buildLoginRequest(clientId: string) {
  return {
    scopes: getApiScopes(clientId),
  }
}
