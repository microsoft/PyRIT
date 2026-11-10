import { act, renderHook } from '@testing-library/react'

import { promptGenerationsApi } from '@/services/api'
import { makePreflight } from '@/test-utils/promptGenerationFixtures'
import type { PromptGenerationCreateRequest } from '@/types'

import { PREFLIGHT_DEBOUNCE_MS, usePromptGenerationPreflight } from './usePromptGenerationPreflight'

jest.mock('@/services/api', () => ({
  promptGenerationsApi: { preflight: jest.fn() },
}))

const mockPreflight = jest.mocked(promptGenerationsApi.preflight)

function request(datasetName: string): PromptGenerationCreateRequest {
  return {
    generator: 'target_objective',
    dataset_name: datasetName,
    request: { instructions: 'x', count: 1, harm_categories: [] },
    targets: {},
    pause_for_review: false,
  }
}

async function advance(milliseconds: number): Promise<void> {
  await act(async () => {
    await jest.advanceTimersByTimeAsync(milliseconds)
  })
}

describe('usePromptGenerationPreflight', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    jest.useFakeTimers()
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  it('stays idle and sends nothing without requests', async () => {
    const { result } = renderHook(() => usePromptGenerationPreflight(null))
    await advance(PREFLIGHT_DEBOUNCE_MS)

    expect(result.current).toEqual({ status: 'idle', results: [], error: null })
    expect(mockPreflight).not.toHaveBeenCalled()
  })

  it('checks only after the inputs settle and keeps the last result while checking again', async () => {
    mockPreflight.mockResolvedValue(makePreflight())
    const { result, rerender } = renderHook(
      ({ requests }: { requests: PromptGenerationCreateRequest[] }) => usePromptGenerationPreflight(requests),
      { initialProps: { requests: [request('a')] } },
    )
    expect(result.current.status).toBe('checking')
    rerender({ requests: [request('ab')] })
    await advance(PREFLIGHT_DEBOUNCE_MS - 1)
    expect(mockPreflight).not.toHaveBeenCalled()
    rerender({ requests: [request('abc')] })
    await advance(PREFLIGHT_DEBOUNCE_MS)

    expect(mockPreflight).toHaveBeenCalledTimes(1)
    expect(mockPreflight).toHaveBeenCalledWith(request('abc'), expect.any(AbortSignal))
    expect(result.current).toEqual({ status: 'ready', results: [makePreflight()], error: null })

    rerender({ requests: [request('abcd'), request('second')] })
    expect(result.current.status).toBe('checking')
    expect(result.current.results).toEqual([makePreflight()])
    await advance(PREFLIGHT_DEBOUNCE_MS)
    expect(mockPreflight).toHaveBeenCalledTimes(3)
    expect(result.current.results).toHaveLength(2)
  })

  it('reports a failed check', async () => {
    mockPreflight.mockRejectedValue(new Error('Offline'))
    const { result } = renderHook(() => usePromptGenerationPreflight([request('a')]))
    await advance(PREFLIGHT_DEBOUNCE_MS)

    expect(result.current).toEqual({ status: 'error', results: [], error: 'Offline' })
  })
})
