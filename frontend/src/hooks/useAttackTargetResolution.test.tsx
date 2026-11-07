import { renderHook, waitFor } from '@testing-library/react'

import { targetsApi } from '@/services/api'
import type { TargetInfo, TargetInstance } from '@/types'

import { useAttackTargetResolution } from './useAttackTargetResolution'

let mockRuntimeGeneration = 'generation-1'

jest.mock('@/hooks/useRuntime', () => ({
  useRuntime: () => ({ generation: mockRuntimeGeneration, ready: true, state: 'ready' }),
}))

jest.mock('@/services/api', () => ({
  targetsApi: { getTarget: jest.fn(), buildTarget: jest.fn() },
}))

jest.mock('@/services/targetRegistry', () => ({
  listRegisteredTargets: jest.fn(),
}))

const createdTarget: TargetInstance = {
  target_registry_name: 'target',
  identifier: { class_name: 'TextTarget', hash: 'target-hash' },
}
const replacementTarget: TargetInstance = {
  target_registry_name: 'target',
  identifier: { class_name: 'TextTarget', hash: 'target-hash' },
}
const targetInfo: TargetInfo = {
  target_type: 'TextTarget',
  target_registry_name: 'target',
  identifier_hash: 'target-hash',
}

describe('useAttackTargetResolution', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockRuntimeGeneration = 'generation-1'
    jest.mocked(targetsApi.getTarget).mockResolvedValue(replacementTarget)
  })

  it('reconstructs a saved temperature without selecting the registered default', async () => {
    const binding = {
      version: 1 as const, source_name: 'target', source_hash: 'source-hash',
      temperature: 0.8, effective_hash: 'effective-hash',
    }
    jest.mocked(targetsApi.buildTarget).mockResolvedValue({
      identifier: { class_name: 'OpenAIChatTarget', hash: 'effective-hash', temperature: 0.8 },
    })
    const savedTarget: TargetInfo = {
      ...targetInfo, target_type: 'OpenAIChatTarget', identifier_hash: 'effective-hash', binding,
    }
    const { result, rerender } = renderHook(() => useAttackTargetResolution({
      attackId: 'saved', attackLoadSequence: 1,
      attackTarget: savedTarget,
      attackTargetSource: 'persisted',
    }))
    await waitFor(() => expect(result.current.activeTarget?.binding).toEqual(binding))
    expect(targetsApi.getTarget).not.toHaveBeenCalled()
    expect(targetsApi.buildTarget).toHaveBeenCalledWith('OpenAIChatTarget', {
      source_name: 'target', source_hash: 'source-hash', params: { temperature: 0.8 },
      effective_hash: 'effective-hash',
    })
    jest.mocked(targetsApi.buildTarget).mockRejectedValue(new Error('Source missing'))
    mockRuntimeGeneration = 'generation-2'
    rerender()
    expect(result.current.activeTarget).toBeNull()
    await waitFor(() => expect(result.current.resolutionStatus).toBe('error'))
    expect(result.current.activeTarget).toBeNull()
  })

  it('resolves a created attack from the new registry after the runtime generation changes', async () => {
    const { result, rerender } = renderHook(() => useAttackTargetResolution({
      attackId: 'attack-id',
      attackLoadSequence: 1,
      attackTarget: targetInfo,
      attackTargetSource: 'created',
      createdTarget,
      createdTargetGeneration: 'generation-1',
    }))

    expect(result.current.activeTarget).toBe(createdTarget)
    expect(targetsApi.getTarget).not.toHaveBeenCalled()

    mockRuntimeGeneration = 'generation-2'
    rerender()

    await waitFor(() => expect(result.current.activeTarget).toBe(replacementTarget))
    expect(targetsApi.getTarget).toHaveBeenCalledWith('target')
  })

  it('distinguishes a saved unbound draft from a legacy missing target after reload', () => {
    const { result, rerender } = renderHook(({ unbound }: { unbound: boolean }) => useAttackTargetResolution({
      attackId: 'saved', attackLoadSequence: 1, attackTarget: null,
      attackTargetSource: 'persisted', targetUnbound: unbound,
    }), { initialProps: { unbound: true } })
    expect(result.current.resolutionStatus).toBe('unbound')
    expect(result.current.activeTarget).toBeNull()
    rerender({ unbound: false })
    expect(result.current.resolutionStatus).toBe('legacy')
    expect(targetsApi.getTarget).not.toHaveBeenCalled()
  })
})
