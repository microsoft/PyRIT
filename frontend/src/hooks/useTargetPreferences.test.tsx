import { act, renderHook } from '@testing-library/react'

import { makeTarget } from '@/test-utils/targetFixtures'
import { readTargetPreferences, writeTargetPreferences } from '@/utils/targetPreferences'

import { useTargetPreferences } from './useTargetPreferences'

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

  it('stores only target references and restores each account independently', () => {
    const first = renderHook(() => useTargetPreferences('tenant:alice', [target, adversarial]))
    act(() => first.result.current.setDefault('objective', target))
    act(() => first.result.current.setDefault('adversarial', adversarial))
    expect(readTargetPreferences('tenant:alice')).toEqual({
      objective: { registryName: 'objective', identifierHash: 'objective-hash' },
      adversarial: { registryName: 'adversarial', identifierHash: 'adversarial-hash' },
    })
    first.unmount()

    const restored = renderHook(() => useTargetPreferences('tenant:alice', [target, adversarial]))
    expect(restored.result.current.objectiveTarget).toBe(target)
    expect(restored.result.current.adversarialTarget).toBe(adversarial)
    const other = renderHook(() => useTargetPreferences('tenant:bob', [target, adversarial]))
    expect(other.result.current.objectiveTarget).toBeNull()
    expect(other.result.current.adversarialTarget).toBeNull()
    act(() => restored.result.current.setDefault('objective', null))
    expect(readTargetPreferences('tenant:alice').objective).toBeNull()
    expect(readTargetPreferences('tenant:alice').adversarial?.registryName).toBe('adversarial')
  })

  it('does not use the local profile before a signed-in identity is ready', () => {
    writeTargetPreferences('local', {
      objective: { registryName: 'objective', identifierHash: 'objective-hash' },
      adversarial: null,
    })

    const { result } = renderHook(() => useTargetPreferences(null, [target]))
    expect(result.current.objectiveTarget).toBeNull()
    act(() => result.current.setDefault('objective', target))
    expect(result.current.error).toContain('Could not save')
    expect(window.localStorage.length).toBe(1)
  })

  it('keeps both defaults when they change in the same event', () => {
    const { result } = renderHook(() => useTargetPreferences('alice', [target, adversarial]))
    act(() => {
      result.current.setDefault('objective', target)
      result.current.setDefault('adversarial', adversarial)
    })
    expect(result.current.objectiveTarget).toBe(target)
    expect(result.current.adversarialTarget).toBe(adversarial)
    expect(readTargetPreferences('alice')).toEqual(result.current.preferences)
  })

  it('rejects changed identities and ineligible adversarial defaults', () => {
    const { result, rerender } = renderHook(
      ({ targets }) => useTargetPreferences('alice', targets),
      { initialProps: { targets: [target, adversarial] } },
    )
    act(() => result.current.setDefault('objective', target))
    act(() => result.current.setDefault('adversarial', adversarial))
    rerender({
      targets: [
        makeTarget({ target_registry_name: 'objective', identifier_hash: 'changed-hash' }),
        { ...adversarial, capabilities: { supports_multi_turn: false } },
      ],
    })
    expect(result.current.objectiveTarget).toBeNull()
    expect(result.current.adversarialTarget).toBeNull()
    expect(result.current.preferences.objective?.registryName).toBe('objective')
  })

  it('reports invalid storage and allows a new choice', () => {
    window.localStorage.setItem('pyrit.targetDefaults.v1.alice', '{"objective":123}')
    const { result } = renderHook(() => useTargetPreferences('alice', [target]))
    expect(result.current.error).toContain('Could not read')
    act(() => result.current.setDefault('objective', target))
    expect(result.current.error).toBeNull()
    expect(result.current.objectiveTarget).toBe(target)
  })

  it('keeps in-memory choices and reports failed storage writes', () => {
    jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('Storage unavailable')
    })
    const { result } = renderHook(() => useTargetPreferences('alice', [target]))
    act(() => result.current.setDefault('objective', target))
    expect(result.current.objectiveTarget).toBe(target)
    expect(result.current.error).toContain('session only')
  })
})
