import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import type { UserPreferences } from '@/types'
import {
  DEFAULT_USER_PREFERENCES,
  readUserPreferences,
  userPreferencesStorageKey,
  writeUserPreferences,
} from '@/utils/userPreferences'

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
      error: 'Could not read saved user preferences.',
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
  const pendingUpdates = useRef<Array<(current: UserPreferences) => UserPreferences>>([])

  useEffect(() => {
    if (accountKey === null) return
    const synchronize = (event: StorageEvent): void => {
      if (event.storageArea !== window.localStorage
        || (event.key !== null && event.key !== userPreferencesStorageKey(accountKey))) return
      const stored = readPreferences(accountKey)
      if (stored.error) {
        setState({ preferences: preferencesRef.current, error: stored.error })
        return
      }
      const preferences = pendingUpdates.current.reduce(
        (current: UserPreferences, update) => update(current), stored.preferences,
      )
      preferencesRef.current = preferences
      const hasPendingUpdates = pendingUpdates.current.length > 0
      setState((current: PreferenceState) => ({
        preferences,
        error: hasPendingUpdates ? current.error : null,
      }))
    }
    window.addEventListener('storage', synchronize)
    return () => { window.removeEventListener('storage', synchronize) }
  }, [accountKey])

  const updatePreferences = useCallback((update: (current: UserPreferences) => UserPreferences): void => {
    const preferences = update(preferencesRef.current)
    preferencesRef.current = preferences
    pendingUpdates.current.push(update)
    setState((current: PreferenceState) => ({ preferences, error: current.error }))

    const reportSaveFailure = (): void => {
      setState({
        preferences: preferencesRef.current,
        error: 'Preferences apply in this session only. Could not save them in this browser.',
      })
    }
    const persist = (): void => {
      if (pendingUpdates.current.length === 0) return
      try {
        if (accountKey === null) throw new Error('Account identity is not ready.')
        const stored = readPreferences(accountKey)
        const merged = stored.error ? preferencesRef.current : pendingUpdates.current.reduce(
          (current: UserPreferences, applyUpdate) => applyUpdate(current), stored.preferences,
        )
        writeUserPreferences(accountKey, merged)
        pendingUpdates.current = []
        preferencesRef.current = merged
        setState({ preferences: merged, error: null })
      } catch {
        reportSaveFailure()
      }
    }
    // Serialize cross-tab read/modify/write on HTTPS and localhost. Older or
    // insecure browsers still merge with the latest stored values on each edit.
    if (accountKey !== null && navigator.locks) {
      void navigator.locks.request(userPreferencesStorageKey(accountKey), persist).catch(reportSaveFailure)
    } else {
      persist()
    }
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
