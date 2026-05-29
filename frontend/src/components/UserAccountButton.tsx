// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import {
  Avatar,
  Button,
  Popover,
  PopoverSurface,
  PopoverTrigger,
  Text,
} from '@fluentui/react-components'
import { PersonRegular } from '@fluentui/react-icons'
import { useMsal } from '@azure/msal-react'
import { useAuthConfig } from '../auth/AuthConfigContext'
import { buildLoginRequest } from '../auth/msalConfig'
import { useUserAccountButtonStyles } from './UserAccountButton.styles'

/** Renders account info when inside MsalProvider (auth enabled). */
function MsalAccountButton() {
  const styles = useUserAccountButtonStyles()
  const { instance, accounts } = useMsal()
  const config = useAuthConfig()
  const account = instance.getActiveAccount() ?? accounts[0]

  if (!account) {
    return (
      <div className={styles.wrapper}>
        <Button
          appearance="subtle"
          icon={<PersonRegular />}
          onClick={() => instance.loginRedirect(buildLoginRequest(config.clientId)).catch((error) => {
            console.error('Login redirect failed:', error)
          })}
        >
          Log In
        </Button>
      </div>
    )
  }

  const displayName = account.name || account.username || 'User'
  const initials = displayName
    .split(' ')
    .map((part) => part[0])
    .join('')
    .slice(0, 2)
    .toUpperCase()
  return (
    <div className={styles.wrapper}>
      <Popover withArrow>
        <PopoverTrigger disableButtonEnhancement>
          <Button appearance="subtle" icon={<Avatar name={displayName} initials={initials} size={28} />}>
            {displayName}
          </Button>
        </PopoverTrigger>
        <PopoverSurface>
          <div className={styles.popoverContent}>
            <Text className={styles.accountName}>{displayName}</Text>
            {account.username && (
              <Text className={styles.accountEmail}>{account.username}</Text>
            )}
            <Button
              appearance="secondary"
              onClick={() => instance.logoutRedirect().catch((error) => {
                console.error('Logout redirect failed:', error)
              })}
            >
              Sign Out
            </Button>
          </div>
        </PopoverSurface>
      </Popover>
    </div>
  )
}

/**
 * Shows the current user's name (with a popover) or a Log In button.
 * Returns null when auth is disabled (local dev).
 */
export function UserAccountButton() {
  const config = useAuthConfig()

  // Auth disabled — no MsalProvider in tree
  if (!config.clientId || !config.tenantId) {
    return null
  }

  return <MsalAccountButton />
}
