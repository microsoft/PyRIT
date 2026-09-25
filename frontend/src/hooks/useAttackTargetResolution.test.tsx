import { renderHook } from '@testing-library/react'

import { useAttackTargetResolution } from './useAttackTargetResolution'
import { targetsApi } from '@/services/api'

jest.mock('@/services/api', () => ({
  targetsApi: { getTarget: jest.fn(), listTargets: jest.fn() },
}))

describe('useAttackTargetResolution', () => {
  beforeEach(() => jest.clearAllMocks())

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
    expect(targetsApi.listTargets).not.toHaveBeenCalled()
  })
})
