import { act, render, renderHook, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import userEvent from '@testing-library/user-event'

import type { AttackSummary, MessageSendStatus, UserPreferences } from '@/types'
import { attacksApi } from '@/services/api'
import { deferred } from '@/test-utils/deferred'
import { makeTarget } from '@/test-utils/targetFixtures'
import { DEFAULT_USER_PREFERENCES, readUserPreferences, writeUserPreferences } from '@/utils/userPreferences'

import { UserPreferencesProvider, useUserPreferences } from './useUserPreferences'

jest.mock('@/services/api', () => ({
  attacksApi: { getMessageSend: jest.fn(), getAttack: jest.fn() },
}))

const chosenPreferences: UserPreferences = {
  targets: {
    adversarial: { registryName: 'adversarial', identifierHash: 'adversarial-hash' },
  },
  lastUsedTargets: {
    objective: { registryName: 'objective', identifierHash: 'objective-hash' },
    adversarial: { registryName: 'adversarial', identifierHash: 'adversarial-hash' },
  },
  hiddenTargetRegistryNames: ['hidden-target'],
  labels: { campaign: 'demo', team: 'red', removed: null },
  operation: { id: 'operation-demo' },
  theme: 'dark',
  chatMarkdown: true,
  chatScorer: { registry_name: 'judge', identifier_hash: 'full-identity' },
  sidebarExpanded: false,
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
    jest.clearAllMocks()
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
      result.current.updatePreferences((current) => ({ ...current, labels: { campaign: 'new' } }))
      result.current.updatePreferences((current) => ({ ...current, operation: chosenPreferences.operation }))
      result.current.updatePreferences((current) => ({ ...current, targets: chosenPreferences.targets }))
      result.current.updatePreferences((current) => ({ ...current, lastUsedTargets: chosenPreferences.lastUsedTargets }))
      result.current.updatePreferences((current) => ({
        ...current,
        hiddenTargetRegistryNames: chosenPreferences.hiddenTargetRegistryNames,
      }))
      result.current.updatePreferences((current) => ({ ...current, chatMarkdown: true }))
      result.current.updatePreferences((current) => ({ ...current, chatScorer: chosenPreferences.chatScorer }))
      result.current.updatePreferences((current) => ({ ...current, sidebarExpanded: false }))
    })
    expect(readUserPreferences('tenant:alice')).toEqual({
      ...chosenPreferences,
      labels: { campaign: 'new' },
    })
  })

  it('merges independent target changes even before a cross-tab event arrives', () => {
    const first = renderHook(() => useUserPreferences(), { wrapper })
    const second = renderHook(() => useUserPreferences(), { wrapper })
    act(() => first.result.current.updatePreferences((current: UserPreferences) => ({
      ...current, lastUsedTargets: chosenPreferences.lastUsedTargets,
    })))
    act(() => second.result.current.updatePreferences((current: UserPreferences) => ({
      ...current, targets: { ...current.targets, adversarial: chosenPreferences.targets.adversarial },
    })))
    expect(readUserPreferences('tenant:alice').targets).toEqual(chosenPreferences.targets)
    expect(readUserPreferences('tenant:alice').lastUsedTargets).toEqual(chosenPreferences.lastUsedTargets)
  })

  it('merges hidden targets selected in two tabs', () => {
    const first = renderHook(() => useUserPreferences(), { wrapper })
    const second = renderHook(() => useUserPreferences(), { wrapper })
    const hideTarget = (registryName: string) => (current: UserPreferences): UserPreferences => ({
      ...current,
      hiddenTargetRegistryNames: [...new Set([...current.hiddenTargetRegistryNames, registryName])].sort(),
    })

    act(() => first.result.current.updatePreferences(hideTarget('target-a')))
    act(() => second.result.current.updatePreferences(hideTarget('target-b')))

    expect(readUserPreferences('tenant:alice').hiddenTargetRegistryNames).toEqual(['target-a', 'target-b'])
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
        lastUsedTargets: chosenPreferences.lastUsedTargets,
      })
      await act(async () => {
        for (const releaseLock of releaseLocks) releaseLock()
      })
      await waitFor(() => expect(readUserPreferences('tenant:alice')).toEqual({
        ...DEFAULT_USER_PREFERENCES, targets: chosenPreferences.targets,
        lastUsedTargets: chosenPreferences.lastUsedTargets, theme: 'dark',
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
    expect(JSON.parse(screen.getByLabelText('Preferences').textContent ?? '')).toEqual(DEFAULT_USER_PREFERENCES)
    rerender(
      <UserPreferencesProvider key="alice" accountKey="tenant:alice"><PreferenceControls /></UserPreferencesProvider>,
    )
    expect(JSON.parse(screen.getByLabelText('Preferences').textContent ?? '')).toEqual(chosenPreferences)
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

  it('defaults hidden targets when reading preferences saved before visibility controls', () => {
    const olderPreferences = {
      targets: chosenPreferences.targets,
      labels: chosenPreferences.labels,
      theme: chosenPreferences.theme,
      chatMarkdown: chosenPreferences.chatMarkdown,
    }
    window.localStorage.setItem('pyrit.userPreferences.v1.tenant:alice', JSON.stringify(olderPreferences))

    expect(readUserPreferences('tenant:alice')).toEqual({
      ...chosenPreferences,
      hiddenTargetRegistryNames: [],
      operation: undefined,
      chatScorer: undefined,
      sidebarExpanded: undefined,
      lastUsedTargets: undefined,
    })
  })

  it.each([
    ['a name', 'op_saved_before_operations', { name: 'op_saved_before_operations' }],
    ['an explicit clear', null, null],
  ])('moves an operation label saved as %s out of labels without resolving it', (
    _description: string, legacy: string | null, expected: { name: string } | null,
  ) => {
    window.localStorage.setItem('pyrit.userPreferences.v1.tenant:alice', JSON.stringify({
      ...chosenPreferences, operation: undefined, labels: { ...chosenPreferences.labels, operation: legacy },
    }))

    expect(readUserPreferences('tenant:alice')).toEqual({ ...chosenPreferences, operation: expected })
  })

  it('migrates legacy local settings on the next write without falling back after a clear', () => {
    window.localStorage.setItem('pyrit.targetDefaults.v1.local', JSON.stringify(chosenPreferences.targets))
    window.localStorage.setItem('pyrit.globalLabels', JSON.stringify({ operation: 'op_legacy', team: 'blue' }))
    window.localStorage.setItem('pyrit.themeMode', 'dark')
    window.localStorage.setItem('pyrit.chatMarkdownMode', 'markdown')
    const preferences = readUserPreferences('local')
    expect(preferences).toEqual({
      ...chosenPreferences,
      hiddenTargetRegistryNames: [],
      labels: { team: 'blue' },
      operation: { name: 'op_legacy' },
      chatScorer: undefined,
      sidebarExpanded: undefined,
      lastUsedTargets: undefined,
    })
    writeUserPreferences('local', DEFAULT_USER_PREFERENCES)
    expect(readUserPreferences('local')).toEqual(DEFAULT_USER_PREFERENCES)
  })

  it.each([
    'not json',
    'null',
    '[]',
    JSON.stringify({ ...chosenPreferences, lastUsedTargets: { objective: { registryName: 'no-hash' }, adversarial: null } }),
    JSON.stringify({ ...chosenPreferences, lastUsedChatScorer: { registry_name: 'no-hash' } }),
    JSON.stringify({ ...chosenPreferences, labels: { bad: 5 } }),
    JSON.stringify({ ...chosenPreferences, theme: 'unknown' }),
    JSON.stringify({ ...chosenPreferences, chatMarkdown: 'markdown' }),
    JSON.stringify({ ...chosenPreferences, sidebarExpanded: 'false' }),
    JSON.stringify({ ...chosenPreferences, hiddenTargetRegistryNames: [5] }),
    JSON.stringify({ ...chosenPreferences, operation: { id: '' } }),
    JSON.stringify({ ...chosenPreferences, operation: { id: 'saved', name: 'both' } }),
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

  it('persists actual-use references and scorer Off without replacing Registry defaults', () => {
    writeUserPreferences('tenant:alice', chosenPreferences)
    const { result, unmount } = renderHook(() => useUserPreferences(), { wrapper })
    act(() => result.current.rememberUse({
      objective: { registryName: 'used', identifierHash: 'used-hash' },
    }, null))
    unmount()
    const restored = renderHook(() => useUserPreferences(), { wrapper })
    expect(restored.result.current.preferences.lastUsedTargets).toEqual({
      objective: { registryName: 'used', identifierHash: 'used-hash' },
      adversarial: chosenPreferences.lastUsedTargets?.adversarial,
    })
    expect(restored.result.current.preferences.lastUsedChatScorer).toBeNull()
    expect(restored.result.current.preferences.chatScorer).toEqual(chosenPreferences.chatScorer)
    expect(restored.result.current.preferences.targets).toEqual(chosenPreferences.targets)
  })

  it('shares scan target recording without changing the last-used chat scorer', () => {
    writeUserPreferences('tenant:alice', { ...chosenPreferences, lastUsedChatScorer: null })
    const target = makeTarget({ target_registry_name: 'scan-target', identifier_hash: 'scan-hash' })
    const adversarial = makeTarget({ target_registry_name: 'scan-adversarial', identifier_hash: 'adversarial-hash' })
    const { result } = renderHook(() => useUserPreferences(), { wrapper })
    act(() => result.current.rememberScanTargets([target, adversarial], {
      target_name: target.target_registry_name, adversarial_target_name: adversarial.target_registry_name,
    }))
    const saved = readUserPreferences('tenant:alice')
    expect(saved.lastUsedTargets).toEqual({
      objective: { registryName: 'scan-target', identifierHash: 'scan-hash' },
      adversarial: { registryName: 'scan-adversarial', identifierHash: 'adversarial-hash' },
    })
    expect(saved.lastUsedChatScorer).toBeNull()
    expect(saved.targets).toEqual(chosenPreferences.targets)
  })

  describe('account-owned confirmed chat use', () => {
    const target = { registryName: 'used-target', identifierHash: 'source-hash' }
    const scorer = { registry_name: 'judge', identifier_hash: 'judge-hash' }
    const catalog = [{
      scorer_registry_name: scorer.registry_name,
      identifier: { class_name: 'SubStringScorer', class_module: 'pyrit.score', hash: scorer.identifier_hash, scorer_type: 'true_false' },
    }]
    const completed: MessageSendStatus = {
      attack_result_id: 'attack', conversation_id: 'conversation', send_id: 'send',
      state: 'completed', request_turn_number: 0, failure_stage: null, error: null,
    }
    const attack: AttackSummary = {
      attack_result_id: 'attack', conversation_id: 'conversation', attack_type: 'ManualAttack',
      objective: 'Goal', chat_scorer: scorer, chat_scorer_inherited: false,
      converters: [], related_conversation_ids: [], message_count: 2, labels: {}, created_at: '', updated_at: '',
    }

    it('records once in the originating account after its provider unmounts', async () => {
      const acceptance = deferred<MessageSendStatus>()
      const progress = deferred<MessageSendStatus>()
      jest.mocked(attacksApi.getMessageSend).mockReturnValue(progress.promise)
      jest.mocked(attacksApi.getAttack).mockResolvedValue(attack)
      const write = jest.spyOn(Storage.prototype, 'setItem')
      const origin = renderHook(() => useUserPreferences(), { wrapper })
      origin.result.current.observeChatSend('attack', acceptance.promise, target, scorer, false, catalog)
      origin.unmount()
      const other = renderHook(() => useUserPreferences(), {
        wrapper: ({ children }: { children: ReactNode }) => (
          <UserPreferencesProvider accountKey="tenant:bob">{children}</UserPreferencesProvider>
        ),
      })
      await act(async () => {
        acceptance.resolve({ ...completed, state: 'queued', request_turn_number: null })
      })
      expect(readUserPreferences('tenant:alice').lastUsedTargets).toBeUndefined()
      await act(async () => { progress.resolve(completed) })
      expect(readUserPreferences('tenant:alice').lastUsedTargets?.objective).toEqual(target)
      expect(readUserPreferences('tenant:alice').lastUsedChatScorer).toEqual(scorer)
      expect(other.result.current.preferences).toEqual(DEFAULT_USER_PREFERENCES)
      expect(readUserPreferences('tenant:bob')).toEqual(DEFAULT_USER_PREFERENCES)
      expect(write).toHaveBeenCalledTimes(1)
    })

    it.each([
      { name: 'preparation failure', request_turn_number: 0, failure_stage: 'preparation' as const },
      { name: 'no confirmed request turn', request_turn_number: null, failure_stage: null },
    ])('does not record $name', async ({ request_turn_number, failure_stage }) => {
      const { result } = renderHook(() => useUserPreferences(), { wrapper })
      await act(async () => {
        result.current.observeChatSend('attack', Promise.resolve({
          ...completed, state: 'failed', request_turn_number, failure_stage,
        }), target, scorer, true, catalog)
      })
      expect(readUserPreferences('tenant:alice')).toEqual(DEFAULT_USER_PREFERENCES)
      expect(attacksApi.getAttack).not.toHaveBeenCalled()
    })

    it.each([
      { name: 'unresolved implicit default', choice: undefined, explicit: false, inherited: false, expected: scorer },
      { name: 'missing selected scorer', choice: scorer, explicit: true, inherited: false, expected: scorer },
      { name: 'explicit Off', choice: null, explicit: true, inherited: false, expected: null },
      { name: 'inherited Off', choice: null, explicit: false, inherited: true, expected: scorer },
    ])('preserves scorer provenance for $name', async ({ choice, explicit, inherited, expected }) => {
      writeUserPreferences('tenant:alice', { ...DEFAULT_USER_PREFERENCES, lastUsedChatScorer: scorer })
      jest.mocked(attacksApi.getAttack).mockResolvedValue({
        ...attack, chat_scorer: null, chat_scorer_inherited: inherited,
      })
      const { result } = renderHook(() => useUserPreferences(), { wrapper })
      await act(async () => {
        result.current.observeChatSend('attack', Promise.resolve(completed), target, choice, explicit, catalog)
      })
      expect(readUserPreferences('tenant:alice').lastUsedTargets?.objective).toEqual(target)
      expect(readUserPreferences('tenant:alice').lastUsedChatScorer).toEqual(expected)
    })

    it('shares receipt reads and reports failed confirmation instead of inventing use', async () => {
      const failure = new Error('Progress unavailable')
      jest.mocked(attacksApi.getMessageSend).mockRejectedValue(failure)
      const { result } = renderHook(() => useUserPreferences(), { wrapper })
      const observation = result.current.observeChatSend(
        'attack', Promise.resolve({ ...completed, state: 'queued' }), target, scorer, false, catalog,
      )
      await act(async () => { await observation.readProgress() })
      await waitFor(() => expect(result.current.error).toContain('Progress unavailable'))
      expect(readUserPreferences('tenant:alice').lastUsedTargets).toBeUndefined()
      expect(attacksApi.getMessageSend).toHaveBeenCalledTimes(1)
    })
  })
})
