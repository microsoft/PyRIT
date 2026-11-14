import { act, renderHook } from '@testing-library/react'

import { attacksApi } from '@/services/api'
import type { AttackMetadata } from '@/types'

import { LIVE_ATTACK_POLL_INTERVAL_MS, useLiveAttackMetadata } from './useLiveAttackMetadata'

jest.mock('@/services/api', () => ({ attacksApi: { getAttackMetadata: jest.fn() } }))

const METADATA: AttackMetadata = {
  attack_result_id: 'live', conversation_id: 'main', objective: 'Goal', labels: {}, execution_read_only: true,
  in_progress: true, related_conversation_count: 0, last_response_message_piece_id: null,
  selected_conversation_id: 'main', selected_conversation_valid: true,
}

async function advance(): Promise<void> {
  await act(async () => { await jest.advanceTimersByTimeAsync(LIVE_ATTACK_POLL_INTERVAL_MS) })
}

describe('useLiveAttackMetadata', () => {
  const onMetadata = jest.fn()
  const onGone = jest.fn()

  beforeEach(() => {
    jest.clearAllMocks()
    jest.useFakeTimers()
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  it('should read until the attack is no longer in progress, retrying failed reads', async () => {
    jest.mocked(attacksApi.getAttackMetadata)
      .mockRejectedValueOnce({ isAxiosError: true, response: { status: 503, data: { detail: 'Busy' } } })
      .mockResolvedValueOnce(METADATA)
      .mockResolvedValueOnce({ ...METADATA, in_progress: false })
    renderHook(() => useLiveAttackMetadata({ attackResultId: 'live', onMetadata, onGone }))
    expect(attacksApi.getAttackMetadata).not.toHaveBeenCalled()
    await advance()
    expect(onMetadata).not.toHaveBeenCalled()
    await advance()
    expect(onMetadata).toHaveBeenLastCalledWith(METADATA)
    await advance()
    expect(onMetadata).toHaveBeenLastCalledWith({ ...METADATA, in_progress: false })
    await advance()
    expect(attacksApi.getAttackMetadata).toHaveBeenCalledTimes(3)
    expect(attacksApi.getAttackMetadata).toHaveBeenCalledWith('live')
    expect(onGone).not.toHaveBeenCalled()
  })

  it('should stop and report an attack that is neither running nor saved', async () => {
    jest.mocked(attacksApi.getAttackMetadata).mockRejectedValue({ isAxiosError: true, response: { status: 404, data: { detail: 'Missing' } } })
    renderHook(() => useLiveAttackMetadata({ attackResultId: 'live', onMetadata, onGone }))
    await advance()
    await advance()
    expect(onGone).toHaveBeenCalledTimes(1)
    expect(attacksApi.getAttackMetadata).toHaveBeenCalledTimes(1)
  })

  it('should not read without an attack or after it stops following', async () => {
    jest.mocked(attacksApi.getAttackMetadata).mockResolvedValue(METADATA)
    const view = renderHook(({ id }: { id: string | null }) => useLiveAttackMetadata({ attackResultId: id, onMetadata, onGone }),
      { initialProps: { id: null as string | null } })
    await advance()
    expect(attacksApi.getAttackMetadata).not.toHaveBeenCalled()
    view.rerender({ id: 'live' })
    await advance()
    view.rerender({ id: null })
    await advance()
    expect(attacksApi.getAttackMetadata).toHaveBeenCalledTimes(1)
  })
})
