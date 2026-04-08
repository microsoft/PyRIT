// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Auth provider that wraps the app in MsalProvider and handles login.
 *
 * - Fetches MSAL config from the backend at startup
 * - Creates MSAL PublicClientApplication instance
 * - Redirects unauthenticated users to Entra ID login
 * - Shows a loading state while auth initializes
 */

import { useState, useEffect, type ReactNode } from 'react'
import {
  PublicClientApplication,
  EventType,
  type AuthenticationResult,
} from '@azure/msal-browser'
import {
  MsalProvider,
  AuthenticatedTemplate,
  UnauthenticatedTemplate,
  useMsal,
} from '@azure/msal-react'
import { fetchAuthConfig, buildMsalConfig, buildLoginRequest, type AuthConfig } from './msalConfig'
import { AuthConfigContext, useAuthConfig } from './AuthConfigContext'
import { setMsalInstance as setApiMsalInstance, setClientId as setApiClientId } from '../services/api'

function LoginRedirect() {
  const { instance } = useMsal()
  const config = useAuthConfig()

  useEffect(() => {
    instance.loginRedirect(buildLoginRequest(config.clientId)).catch((error) => {
      console.error('Login redirect failed:', error)
    })
  }, [instance, config])

  return <div style={{ padding: '2rem', textAlign: 'center' }}>Redirecting to login...</div>
}

interface AuthProviderProps {
  children: ReactNode
}

export function AuthProvider({ children }: AuthProviderProps) {
  const [msalInstance, setMsalInstance] = useState<PublicClientApplication | null>(null)
  const [authDisabled, setAuthDisabled] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [authConfig, setAuthConfig] = useState<AuthConfig>({clientId: '', tenantId: '', allowedGroupIds: ''})

  useEffect(() => {
    let cancelled = false

    async function initMsal() {
      try {
        const config = await fetchAuthConfig()

        // If no auth config (local dev), skip MSAL entirely
        if (!config.clientId || !config.tenantId) {
          if (!cancelled) {
            setMsalInstance(null) // null signals "auth disabled"
            setAuthDisabled(true)
            setAuthConfig(config)
          }
          return
        }

        const msalConfig = buildMsalConfig(config)
        const instance = new PublicClientApplication(msalConfig)
        await instance.initialize()

        // Handle redirect response (after coming back from login)
        instance.addEventCallback((event) => {
          if (event.eventType === EventType.LOGIN_SUCCESS && event.payload) {
            const result = event.payload as AuthenticationResult
            instance.setActiveAccount(result.account)
          }
        })

        // Await the redirect promise FIRST — on the initial redirect back
        // from Entra, this caches the token and returns the auth result.
        // On normal page loads (no redirect hash) it resolves to null.
        const redirectResult = await instance.handleRedirectPromise()
        if (redirectResult?.account) {
          instance.setActiveAccount(redirectResult.account)
        }

        // Fall back to any cached account from a previous session
        if (!instance.getActiveAccount()) {
          const accounts = instance.getAllAccounts()
          if (accounts.length > 0) {
            instance.setActiveAccount(accounts[0])
          }
        }

        if (!cancelled) {
          // Wire MSAL into the API client BEFORE React re-render,
          // so child components' effects already have the token available.
          setApiMsalInstance(instance)
          setApiClientId(config.clientId)
          setMsalInstance(instance)
          setAuthConfig(config)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to initialize authentication')
        }
      }
    }

    initMsal()
    return () => {
      cancelled = true
    }
  }, [])

  if (error) {
    return (
      <div style={{ padding: '2rem', textAlign: 'center', color: 'red' }}>
        <h2>Authentication Error</h2>
        <p>{error}</p>
      </div>
    )
  }

  if (!msalInstance) {
    // Auth disabled (local dev) — render children directly
    if (authDisabled) {
      return <>{children}</>
    }
    return <div style={{ padding: '2rem', textAlign: 'center' }}>Initializing authentication...</div>
  }

  return (
   <AuthConfigContext.Provider value={authConfig}>
     <MsalProvider instance={msalInstance}>
       <AuthenticatedTemplate>{children}</AuthenticatedTemplate>
       <UnauthenticatedTemplate>
         <LoginRedirect />
       </UnauthenticatedTemplate>
     </MsalProvider>
   </AuthConfigContext.Provider>
  )
}
