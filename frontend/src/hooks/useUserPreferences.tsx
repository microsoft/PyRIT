import { createContext, useCallback, useContext, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import type { UserPreferences } from '@/types'
import { DEFAULT_USER_PREFERENCES, readUserPreferences, writeUserPreferences } from '@/utils/userPreferences'

interface PreferenceState {
  preferences: UserPreferences
  error: string | null
}

interface UserPreferencesContextValue extends PreferenceState {
  updatePreferences: (update: (current: UserPreferences) => UserPreferences) => void
}

const UserPreferencesContext = createContext<UserPreferencesContextValue | null>(null)

function readPreferences(accountKey: string | null): PreferenceState {
  if (accountKey === null) return { preferences: DEFAULT_USER_PREFERENCES, error: null }
  try {
    return { preferences: readUserPreferences(accountKey), error: null }
  } catch {
    return {
      preferences: DEFAULT_USER_PREFERENCES,
      error: 'Could not read saved user preferences. Default settings are in use.',
    }
  }
}

/** Key this provider by account so state and pending updates cannot cross account boundaries. */
export function UserPreferencesProvider({
  accountKey,
  children,
}: {
  accountKey: string | null
  children: ReactNode
}) {
  const [state, setState] = useState<PreferenceState>(() => readPreferences(accountKey))
  const preferencesRef = useRef(state.preferences)
  const updatePreferences = useCallback((update: (current: UserPreferences) => UserPreferences): void => {
    const preferences = update(preferencesRef.current)
    preferencesRef.current = preferences
    let error: string | null = null
    try {
      if (accountKey === null) throw new Error('Account identity is not ready.')
      writeUserPreferences(accountKey, preferences)
    } catch {
      error = 'Preferences apply in this session only. Could not save them in this browser.'
    }
    setState({ preferences, error })
  }, [accountKey])

  return (
    <UserPreferencesContext.Provider value={{ ...state, updatePreferences }}>
      {children}
    </UserPreferencesContext.Provider>
  )
}

// eslint-disable-next-line react-refresh/only-export-components
export function useUserPreferences(): UserPreferencesContextValue {
  const context = useContext(UserPreferencesContext)
  if (context === null) throw new Error('User preferences require UserPreferencesProvider.')
  return context
}
