import { act, renderHook, waitFor } from '@testing-library/react'

import { attacksApi } from '@/services/api'
import type { AttackFindingItem } from '@/types'

import { useAttackFindings } from './useAttackFindings'

jest.mock('@/services/api', () => ({ attacksApi: { getAttackFindings: jest.fn() } }))

const FINDING = {
  id: 'finding-1', operation_id: 'operation-1', title: 'Prompt leak', severity: 'high', description: '',
  created_at: '2026-10-08T12:00:00Z', operation_name: 'Review', evidence_id: 'evidence-1',
} as AttackFindingItem

describe('useAttackFindings', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('should load the findings that link an attack', async () => {
    jest.mocked(attacksApi.getAttackFindings).mockResolvedValue({ attack_result_id: 'attack-1', items: [FINDING] })
    const { result } = renderHook(() => useAttackFindings('attack-1'))

    expect(result.current.loading).toBe(true)
    await waitFor(() => { expect(result.current.loading).toBe(false) })
    expect(result.current.findings).toEqual([FINDING])
    expect(result.current.error).toBeNull()
    expect(attacksApi.getAttackFindings).toHaveBeenCalledWith('attack-1')
  })

  it('should not request findings without an attack', () => {
    const { result } = renderHook(() => useAttackFindings(null))
    expect(result.current).toMatchObject({ findings: [], loading: false, error: null })
    expect(attacksApi.getAttackFindings).not.toHaveBeenCalled()
  })

  it('should keep findings visible while a refresh reloads them', async () => {
    jest.mocked(attacksApi.getAttackFindings).mockResolvedValueOnce({ attack_result_id: 'attack-1', items: [FINDING] })
    const { result } = renderHook(() => useAttackFindings('attack-1'))
    await waitFor(() => { expect(result.current.loading).toBe(false) })
    expect(result.current.findings).toEqual([FINDING])

    let resolve: (value: { attack_result_id: string; items: AttackFindingItem[] }) => void = () => {}
    jest.mocked(attacksApi.getAttackFindings).mockReturnValueOnce(new Promise((complete) => { resolve = complete }))
    act(() => { result.current.refresh() })
    expect(result.current.loading).toBe(true)
    expect(result.current.findings).toEqual([FINDING])

    await act(async () => { resolve({ attack_result_id: 'attack-1', items: [] }) })
    expect(result.current.findings).toEqual([])
    expect(attacksApi.getAttackFindings).toHaveBeenCalledTimes(2)
  })

  it('should not show another attack\'s findings after switching attacks', async () => {
    jest.mocked(attacksApi.getAttackFindings).mockResolvedValueOnce({ attack_result_id: 'attack-1', items: [FINDING] })
    const { result, rerender } = renderHook(({ id }: { id: string }) => useAttackFindings(id), { initialProps: { id: 'attack-1' } })
    await waitFor(() => { expect(result.current.findings).toEqual([FINDING]) })

    jest.mocked(attacksApi.getAttackFindings).mockReturnValueOnce(new Promise(() => {}))
    rerender({ id: 'attack-2' })
    expect(result.current.findings).toEqual([])
    expect(result.current.loading).toBe(true)
  })

  it('should report a failed request', async () => {
    jest.mocked(attacksApi.getAttackFindings).mockRejectedValue(new Error('Findings unavailable'))
    const { result } = renderHook(() => useAttackFindings('attack-1'))
    await waitFor(() => { expect(result.current.loading).toBe(false) })
    expect(result.current.error).toBe('Findings unavailable')
    expect(result.current.findings).toEqual([])
  })
})
