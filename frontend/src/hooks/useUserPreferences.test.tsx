import { act, render, renderHook, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import userEvent from '@testing-library/user-event'

import type { UserPreferences } from '@/types'
import { DEFAULT_USER_PREFERENCES, readUserPreferences, writeUserPreferences } from '@/utils/userPreferences'

import { UserPreferencesProvider, useUserPreferences } from './useUserPreferences'

const chosenPreferences: UserPreferences = {
  targets: {
    objective: { registryName: 'objective', identifierHash: 'objective-hash' },
    adversarial: { registryName: 'adversarial', identifierHash: 'adversarial-hash' },
  },
  labels: { operation: 'op_demo', team: 'red', removed: null },
  theme: 'dark',
  chatMarkdown: true,
}

function wrapper({ children }: { children: ReactNode }) {
  return <UserPreferencesProvider accountKey="tenant:alice">{children}</UserPreferencesProvider>
}

function PreferenceControls() {
  const { preferences, error, updatePreferences } = useUserPreferences()
  return (
    <>
      <output aria-label="Preferences">{JSON.stringify(preferences)}</output>
      {error && <p role="alert">{error}</p>}
      <button onClick={() => updatePreferences(() => chosenPreferences)}>Save preferences</button>
    </>
  )
}

describe('useUserPreferences', () => {
  beforeEach(() => {
    window.localStorage.clear()
    jest.restoreAllMocks()
  })

  it('stores and restores every preference in one account-scoped record', () => {
    const first = renderHook(() => useUserPreferences(), { wrapper })
    act(() => first.result.current.updatePreferences(() => chosenPreferences))
    expect(window.localStorage.length).toBe(1)
    expect(JSON.parse(window.localStorage.getItem('pyrit.userPreferences.v1.tenant:alice') ?? '')).toEqual(chosenPreferences)
    first.unmount()
    const restored = renderHook(() => useUserPreferences(), { wrapper })
    expect(restored.result.current.preferences).toEqual(chosenPreferences)
    expect(restored.result.current.error).toBeNull()
  })

  it('keeps changes to different properties made in the same event', () => {
    const { result } = renderHook(() => useUserPreferences(), { wrapper })
    act(() => {
      result.current.updatePreferences((current) => ({ ...current, theme: 'dark' }))
      result.current.updatePreferences((current) => ({ ...current, labels: { operation: 'op_new' } }))
      result.current.updatePreferences((current) => ({ ...current, targets: chosenPreferences.targets }))
      result.current.updatePreferences((current) => ({ ...current, chatMarkdown: true }))
    })
    expect(readUserPreferences('tenant:alice')).toEqual({
      ...chosenPreferences,
      labels: { operation: 'op_new' },
    })
  })

  it('merges independent target changes even before a cross-tab event arrives', () => {
    const first = renderHook(() => useUserPreferences(), { wrapper })
    const second = renderHook(() => useUserPreferences(), { wrapper })
    act(() => first.result.current.updatePreferences((current: UserPreferences) => ({
      ...current, targets: { ...current.targets, objective: chosenPreferences.targets.objective },
    })))
    act(() => second.result.current.updatePreferences((current: UserPreferences) => ({
      ...current, targets: { ...current.targets, adversarial: chosenPreferences.targets.adversarial },
    })))
    expect(readUserPreferences('tenant:alice').targets).toEqual(chosenPreferences.targets)
  })

  it('synchronizes only the current account and handles a cross-tab clear', () => {
    const { result } = renderHook(() => useUserPreferences(), { wrapper })
    writeUserPreferences('tenant:alice', chosenPreferences)
    act(() => {
      window.dispatchEvent(new StorageEvent('storage', {
        key: 'pyrit.userPreferences.v1.tenant:bob', storageArea: window.localStorage,
      }))
    })
    expect(result.current.preferences).toEqual(DEFAULT_USER_PREFERENCES)
    act(() => {
      window.dispatchEvent(new StorageEvent('storage', {
        key: 'pyrit.userPreferences.v1.tenant:alice', storageArea: window.localStorage,
      }))
    })
    expect(result.current.preferences).toEqual(chosenPreferences)
    window.localStorage.clear()
    act(() => {
      window.dispatchEvent(new StorageEvent('storage', { key: null, storageArea: window.localStorage }))
    })
    expect(result.current.preferences).toEqual(DEFAULT_USER_PREFERENCES)
  })

  it('keeps failed local edits while merging later changes from another tab', () => {
    const { result } = renderHook(() => useUserPreferences(), { wrapper })
    const write = jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota') })
    act(() => result.current.updatePreferences((current: UserPreferences) => ({ ...current, theme: 'dark' })))
    write.mockRestore()
    writeUserPreferences('tenant:alice', {
      ...DEFAULT_USER_PREFERENCES, targets: chosenPreferences.targets,
    })
    act(() => {
      window.dispatchEvent(new StorageEvent('storage', {
        key: 'pyrit.userPreferences.v1.tenant:alice', storageArea: window.localStorage,
      }))
    })
    expect(result.current.preferences.theme).toBe('dark')
    expect(result.current.error).toContain('session only')
    act(() => result.current.updatePreferences((current: UserPreferences) => ({ ...current, chatMarkdown: true })))
    expect(readUserPreferences('tenant:alice')).toEqual({
      ...DEFAULT_USER_PREFERENCES, targets: chosenPreferences.targets, theme: 'dark', chatMarkdown: true,
    })
    expect(result.current.error).toBeNull()
  })

  it('merges pending edits with the latest record inside the browser lock', async () => {
    const releaseLocks: Array<() => void> = []
    const request = jest.fn((_name: string, callback: () => void) => new Promise<void>((resolve) => {
      releaseLocks.push(() => { callback(); resolve() })
    }))
    const originalLocks = Object.getOwnPropertyDescriptor(navigator, 'locks')
    Object.defineProperty(navigator, 'locks', { configurable: true, value: { request } })
    try {
      const { result } = renderHook(() => useUserPreferences(), { wrapper })
      act(() => result.current.updatePreferences((current: UserPreferences) => ({
        ...current, targets: { ...current.targets, adversarial: chosenPreferences.targets.adversarial },
      })))
      act(() => result.current.updatePreferences((current: UserPreferences) => ({ ...current, theme: 'dark' })))
      expect(result.current.preferences.targets.adversarial).toEqual(chosenPreferences.targets.adversarial)
      writeUserPreferences('tenant:alice', {
        ...DEFAULT_USER_PREFERENCES,
        targets: { objective: chosenPreferences.targets.objective, adversarial: null },
      })
      await act(async () => {
        for (const releaseLock of releaseLocks) releaseLock()
      })
      await waitFor(() => expect(readUserPreferences('tenant:alice')).toEqual({
        ...DEFAULT_USER_PREFERENCES, targets: chosenPreferences.targets, theme: 'dark',
      }))
      expect(request).toHaveBeenCalledWith('pyrit.userPreferences.v1.tenant:alice', expect.any(Function))
    } finally {
      if (originalLocks) Object.defineProperty(navigator, 'locks', originalLocks)
      else Reflect.deleteProperty(navigator, 'locks')
    }
  })

  it('reports a lock failure and keeps the edit in memory', async () => {
    const originalLocks = Object.getOwnPropertyDescriptor(navigator, 'locks')
    Object.defineProperty(navigator, 'locks', {
      configurable: true,
      value: { request: jest.fn().mockRejectedValue(new Error('Lock denied')) },
    })
    try {
      const { result } = renderHook(() => useUserPreferences(), { wrapper })
      act(() => result.current.updatePreferences((current: UserPreferences) => ({ ...current, theme: 'dark' })))
      await waitFor(() => expect(result.current.error).toContain('session only'))
      expect(result.current.preferences.theme).toBe('dark')
      expect(readUserPreferences('tenant:alice').theme).toBe(DEFAULT_USER_PREFERENCES.theme)
    } finally {
      if (originalLocks) Object.defineProperty(navigator, 'locks', originalLocks)
      else Reflect.deleteProperty(navigator, 'locks')
    }
  })

  it('isolates every property when accounts change and restores them when switching back', async () => {
    const user = userEvent.setup()
    const { rerender } = render(
      <UserPreferencesProvider key="alice" accountKey="tenant:alice"><PreferenceControls /></UserPreferencesProvider>,
    )
    await user.click(screen.getByRole('button', { name: 'Save preferences' }))
    rerender(
      <UserPreferencesProvider key="bob" accountKey="tenant:bob"><PreferenceControls /></UserPreferencesProvider>,
    )
    expect(screen.getByLabelText('Preferences')).toHaveTextContent(JSON.stringify(DEFAULT_USER_PREFERENCES))
    rerender(
      <UserPreferencesProvider key="alice" accountKey="tenant:alice"><PreferenceControls /></UserPreferencesProvider>,
    )
    expect(screen.getByLabelText('Preferences')).toHaveTextContent(JSON.stringify(chosenPreferences))
  })

  it('reads old account-scoped targets without importing browser-wide labels or display choices', () => {
    window.localStorage.setItem('pyrit.targetDefaults.v1.tenant:alice', JSON.stringify(chosenPreferences.targets))
    window.localStorage.setItem('pyrit.globalLabels', JSON.stringify({ operation: 'someone_else' }))
    window.localStorage.setItem('pyrit.themeMode', 'dark')
    window.localStorage.setItem('pyrit.chatMarkdownMode', 'markdown')
    const { result } = renderHook(() => useUserPreferences(), { wrapper })
    expect(result.current.preferences).toEqual({
      ...DEFAULT_USER_PREFERENCES,
      targets: chosenPreferences.targets,
    })
    act(() => result.current.updatePreferences((current) => ({ ...current, theme: 'light' })))
    expect(readUserPreferences('tenant:alice').targets).toEqual(chosenPreferences.targets)
    expect(readUserPreferences('tenant:bob')).toEqual(DEFAULT_USER_PREFERENCES)
  })

  it('migrates legacy local settings on the next write without falling back after a clear', () => {
    window.localStorage.setItem('pyrit.targetDefaults.v1.local', JSON.stringify(chosenPreferences.targets))
    window.localStorage.setItem('pyrit.globalLabels', JSON.stringify({ operation: 'op_legacy', team: 'blue' }))
    window.localStorage.setItem('pyrit.themeMode', 'dark')
    window.localStorage.setItem('pyrit.chatMarkdownMode', 'markdown')
    const preferences = readUserPreferences('local')
    expect(preferences).toEqual({
      ...chosenPreferences,
      labels: { operation: 'op_legacy', team: 'blue' },
    })
    writeUserPreferences('local', DEFAULT_USER_PREFERENCES)
    expect(readUserPreferences('local')).toEqual(DEFAULT_USER_PREFERENCES)
  })

  it.each([
    'not json',
    'null',
    '[]',
    JSON.stringify({ ...chosenPreferences, targets: { objective: { registryName: 'no-hash' }, adversarial: null } }),
    JSON.stringify({ ...chosenPreferences, labels: { bad: 5 } }),
    JSON.stringify({ ...chosenPreferences, theme: 'unknown' }),
    JSON.stringify({ ...chosenPreferences, chatMarkdown: 'markdown' }),
  ])('reports invalid stored preferences instead of silently accepting them: %s', (stored: string) => {
    window.localStorage.setItem('pyrit.userPreferences.v1.tenant:alice', stored)
    const { result } = renderHook(() => useUserPreferences(), { wrapper })
    expect(result.current.preferences).toEqual(DEFAULT_USER_PREFERENCES)
    expect(result.current.error).toContain('Could not read')
    act(() => result.current.updatePreferences(() => chosenPreferences))
    expect(result.current.error).toBeNull()
    expect(readUserPreferences('tenant:alice')).toEqual(chosenPreferences)
  })

  it('reports unavailable storage and keeps edits in memory', async () => {
    jest.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied') })
    jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota') })
    const user = userEvent.setup()
    render(<PreferenceControls />, { wrapper })
    expect(screen.getByRole('alert')).toHaveTextContent('Could not read')
    await user.click(screen.getByRole('button', { name: 'Save preferences' }))
    expect(screen.getByRole('alert')).toHaveTextContent('session only')
    expect(screen.getByLabelText('Preferences')).toHaveTextContent(JSON.stringify(chosenPreferences))
  })
})
