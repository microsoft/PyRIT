import { act, renderHook } from '@testing-library/react'
import type { ReactNode } from 'react'

import { makeTarget } from '@/test-utils/targetFixtures'
import type { TargetInstance } from '@/types'
import { DEFAULT_USER_PREFERENCES, readUserPreferences, writeUserPreferences } from '@/utils/userPreferences'
import { targetReference } from '@/utils/targetIdentity'

import { useTargetPreferences } from './useTargetPreferences'
import { UserPreferencesProvider, useUserPreferences } from './useUserPreferences'

function renderTargetPreferences(accountKey: string | null, targets: TargetInstance[]) {
  return renderHook(
    ({ targets: registryTargets }) => {
      const { error, rememberUse } = useUserPreferences()
      return { ...useTargetPreferences(registryTargets), error, rememberUse }
    },
    {
      initialProps: { targets },
      wrapper: ({ children }: { children: ReactNode }) => (
        <UserPreferencesProvider accountKey={accountKey}>{children}</UserPreferencesProvider>
      ),
    },
  )
}

const target = makeTarget({ target_registry_name: 'objective', identifier_hash: 'objective-hash' })
const adversarial = makeTarget({
  target_registry_name: 'adversarial',
  identifier_hash: 'adversarial-hash',
  capabilities: { supports_multi_turn: true },
})

describe('useTargetPreferences', () => {
  beforeEach(() => {
    window.localStorage.clear()
    jest.restoreAllMocks()
  })

  it('restores a temperature-bound source for later chat and scan defaults', () => {
    const bound = {
      ...target, identifier: { ...target.identifier, hash: 'effective-hash' },
      binding: {
        version: 1 as const, source_name: target.target_registry_name, source_hash: target.identifier.hash,
        effective_hash: 'effective-hash', temperature: 0.8,
      },
    }
    const first = renderTargetPreferences('alice', [target, adversarial])
    act(() => first.result.current.rememberUse({ objective: targetReference(bound) }))
    first.unmount()
    const restored = renderTargetPreferences('alice', [target, adversarial])
    expect(restored.result.current.objectiveTarget).toBe(target)
    expect(restored.result.current.objectiveReference).toEqual(targetReference(target))
    expect(bound.identifier.hash).toBe('effective-hash')
  })

  it('stores only target references and restores each account independently', () => {
    const first = renderTargetPreferences('tenant:alice', [target, adversarial])
    act(() => first.result.current.rememberUse({ objective: targetReference(target) }))
    act(() => first.result.current.setDefault('adversarial', adversarial))
    expect(readUserPreferences('tenant:alice').targets).toEqual({
      adversarial: { registryName: 'adversarial', identifierHash: 'adversarial-hash' },
    })
    expect(readUserPreferences('tenant:alice').lastUsedTargets?.objective).toEqual(targetReference(target))
    first.unmount()

    const restored = renderTargetPreferences('tenant:alice', [target, adversarial])
    expect(restored.result.current.objectiveTarget).toBe(target)
    expect(restored.result.current.adversarialTarget).toBe(adversarial)
    const other = renderTargetPreferences('tenant:bob', [target, adversarial])
    expect(other.result.current.objectiveTarget).toBeNull()
    expect(other.result.current.adversarialTarget).toBeNull()
    act(() => restored.result.current.rememberUse({ objective: null }))
    expect(readUserPreferences('tenant:alice').lastUsedTargets?.objective).toBeNull()
    expect(readUserPreferences('tenant:alice').targets.adversarial?.registryName).toBe('adversarial')
  })

  it('does not use the local profile before a signed-in identity is ready', () => {
    writeUserPreferences('local', {
      ...DEFAULT_USER_PREFERENCES,
      lastUsedTargets: {
        objective: { registryName: 'objective', identifierHash: 'objective-hash' },
        adversarial: null,
      },
    })

    const { result } = renderTargetPreferences(null, [target])
    expect(result.current.objectiveTarget).toBeNull()
    act(() => result.current.rememberUse({ objective: targetReference(target) }))
    expect(result.current.error).toContain('Could not save')
    expect(window.localStorage.length).toBe(1)
  })

  it('keeps last use and the configured default when they change in the same event', () => {
    const { result } = renderTargetPreferences('alice', [target, adversarial])
    act(() => {
      result.current.rememberUse({ objective: targetReference(target) })
      result.current.setDefault('adversarial', adversarial)
    })
    expect(result.current.objectiveTarget).toBe(target)
    expect(result.current.adversarialTarget).toBe(adversarial)
    expect(readUserPreferences('alice').targets).toEqual(result.current.preferences)
  })

  it('uses the environment target until the user selects a default, and restores it when cleared', () => {
    const environmentTarget = makeTarget({
      target_registry_name: 'adversarial_chat',
      capabilities: { supports_multi_turn: true },
    })
    const { result, rerender, unmount } = renderTargetPreferences('alice', [])
    expect(result.current.adversarialTarget).toBeNull()
    rerender({ targets: [environmentTarget, adversarial] })
    expect(result.current.adversarialTarget).toBe(environmentTarget)
    expect(window.localStorage.length).toBe(0)

    act(() => result.current.setDefault('adversarial', adversarial))
    expect(result.current.adversarialTarget).toBe(adversarial)
    unmount()
    const restored = renderTargetPreferences('alice', [environmentTarget, adversarial])
    expect(restored.result.current.adversarialTarget).toBe(adversarial)
    act(() => restored.result.current.setDefault('adversarial', null))
    expect(restored.result.current.adversarialTarget).toBe(environmentTarget)
    expect(readUserPreferences('alice').targets.adversarial).toBeNull()
  })

  it('does not replace a saved but unavailable target with the environment target', () => {
    const environmentTarget = makeTarget({
      target_registry_name: 'adversarial_chat',
      capabilities: { supports_multi_turn: true },
    })
    writeUserPreferences('alice', {
      ...DEFAULT_USER_PREFERENCES,
      targets: {
        adversarial: { registryName: 'missing', identifierHash: 'missing-hash' },
      },
    })
    const { result } = renderTargetPreferences('alice', [environmentTarget])
    expect(result.current.adversarialTarget).toBeNull()
  })

  it('does not preselect an environment target without multi-turn support', () => {
    const environmentTarget = makeTarget({ target_registry_name: 'adversarial_chat' })
    const { result } = renderTargetPreferences('alice', [environmentTarget])
    expect(result.current.adversarialTarget).toBeNull()
  })

  it('rejects changed identities and ineligible adversarial defaults', () => {
    const { result, rerender } = renderTargetPreferences('alice', [target, adversarial])
    act(() => result.current.rememberUse({ objective: targetReference(target) }))
    act(() => result.current.setDefault('adversarial', adversarial))
    rerender({
      targets: [
        makeTarget({ target_registry_name: 'objective', identifier_hash: 'changed-hash' }),
        { ...adversarial, capabilities: { supports_multi_turn: false } },
      ],
    })
    expect(result.current.objectiveTarget).toBeNull()
    expect(result.current.adversarialTarget).toBeNull()
    expect(result.current.objectiveReference?.registryName).toBe('objective')
  })

  it('reports invalid storage and allows a new choice', () => {
    window.localStorage.setItem('pyrit.targetDefaults.v1.alice', '{"objective":123}')
    const { result } = renderTargetPreferences('alice', [target])
    expect(result.current.error).toContain('Could not read')
    act(() => result.current.rememberUse({ objective: targetReference(target) }))
    expect(result.current.error).toBeNull()
    expect(result.current.objectiveTarget).toBe(target)
  })

  it('keeps in-memory choices and reports failed storage writes', () => {
    jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('Storage unavailable')
    })
    const { result } = renderTargetPreferences('alice', [target])
    act(() => result.current.rememberUse({ objective: targetReference(target) }))
    expect(result.current.objectiveTarget).toBe(target)
    expect(result.current.error).toContain('session only')
  })

  it('uses the last adversarial scan target only when no registry default exists', () => {
    const { result, rerender } = renderTargetPreferences('alice', [target, adversarial])
    act(() => result.current.rememberUse({ adversarial: targetReference(adversarial) }))
    expect(result.current.scanAdversarialTarget).toBe(adversarial)
    expect(result.current.adversarialTarget).toBeNull()
    const environmentTarget = makeTarget({
      target_registry_name: 'adversarial_chat', capabilities: { supports_multi_turn: true },
    })
    rerender({ targets: [target, adversarial, environmentTarget] })
    expect(result.current.scanAdversarialTarget).toBe(environmentTarget)
    act(() => result.current.setDefault('adversarial', adversarial))
    expect(result.current.scanAdversarialTarget).toBe(adversarial)
  })
})
