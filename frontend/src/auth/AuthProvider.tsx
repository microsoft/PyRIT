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

import { useState, useEffect, useRef, type ReactNode } from 'react'
import { PublicClientApplication } from '@azure/msal-browser'
import {
  MsalProvider,
  AuthenticatedTemplate,
  UnauthenticatedTemplate,
  useMsal,
} from '@azure/msal-react'
import { fetchAuthConfig, buildMsalConfig, buildLoginRequest, type AuthConfig } from './msalConfig'
import { AuthConfigContext } from './AuthConfigContext'
import { setMsalInstance as setApiMsalInstance } from '../services/api'

function LoginRedirect() {
  const { instance } = useMsal()
  const loginStarted = useRef(false)

  useEffect(() => {
    if (loginStarted.current) return
    loginStarted.current = true

    // Capture the path the user originally requested so it can be restored
    // after the login round-trip (see the redirect handling in initMsal).
    instance
      .loginRedirect({
        ...buildLoginRequest(),
        state: window.location.pathname + window.location.search,
      })
      .catch((error) => {
        console.error('Login redirect failed:', error)
      })
  }, [instance])

  return <div style={{ padding: '2rem', textAlign: 'center' }}>Redirecting to login...</div>
}

interface AuthProviderProps {
  children: ReactNode
}

/**
 * True for root-relative paths ("/history") that are safe to restore after a
 * login redirect. Rejects protocol-relative ("//evil.com") and backslash
 * variants ("/\evil.com", which browsers coerce to "//"), since a tampered MSAL
 * state value could otherwise become an open redirect to an external origin. The
 * leading "/\" is rejected explicitly here; the path is never run through a URL
 * parser.
 */
function isSafeInternalPath(path: string): boolean {
  return path.startsWith('/') && !path.startsWith('//') && !path.startsWith('/\\')
}

export function AuthProvider({ children }: AuthProviderProps) {
  const [msalInstance, setMsalInstance] = useState<PublicClientApplication | null>(null)
  const [authDisabled, setAuthDisabled] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [authConfig, setAuthConfig] = useState<AuthConfig>({clientId: '', tenantId: '', allowedGroupIds: ''})
  const initializationStarted = useRef(false)

  useEffect(() => {
    if (initializationStarted.current) return
    initializationStarted.current = true

    async function initMsal() {
      try {
        const config = await fetchAuthConfig()
        setAuthConfig(config)

        if (!config.clientId || !config.tenantId) {
          setAuthDisabled(true)
          return
        }

        const instance = new PublicClientApplication(buildMsalConfig(config))
        await instance.initialize()
        const redirectResult = await instance.handleRedirectPromise({ navigateToLoginRequestUrl: false })

        if (redirectResult?.account) {
          instance.setActiveAccount(redirectResult.account)
        } else if (!instance.getActiveAccount()) {
          const accounts = instance.getAllAccounts()
          if (accounts.length > 0) instance.setActiveAccount(accounts[0])
        }

        const requestedPath = typeof redirectResult?.state === 'string' ? redirectResult.state : null
        if (
          requestedPath &&
          isSafeInternalPath(requestedPath) &&
          requestedPath !== window.location.pathname + window.location.search
        ) {
          window.history.replaceState(null, '', requestedPath)
        }

        // Wire MSAL into the API client BEFORE React re-render,
        // so child components' effects already have the token available.
        setApiMsalInstance(instance)
        setMsalInstance(instance)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to initialize authentication')
      }
    }

    initMsal()
  }, [])

  if (error) {
    return (
      <div style={{ padding: '2rem', textAlign: 'center', color: 'red' }}>
        <h2>Authentication Error</h2>
        <p>{error}</p>
        <p>Reload the page to try again.</p>
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
