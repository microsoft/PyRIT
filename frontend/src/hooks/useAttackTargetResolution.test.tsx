import { renderHook, waitFor } from '@testing-library/react'

import { targetsApi } from '@/services/api'
import type { TargetInfo, TargetInstance } from '@/types'

import { useAttackTargetResolution } from './useAttackTargetResolution'

let mockRuntimeGeneration = 'generation-1'

jest.mock('@/hooks/useRuntime', () => ({
  useRuntime: () => ({ generation: mockRuntimeGeneration, ready: true, state: 'ready' }),
}))

jest.mock('@/services/api', () => ({
  targetsApi: { getTarget: jest.fn() },
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
})
