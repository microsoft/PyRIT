/**
 * Regression tests for working-copy persistence across runtime generation
 * changes (#2867): user-authored working text must survive a generation swap
 * even though generated stage results are invalidated.
 */
import { act, renderHook } from '@testing-library/react'

import type { MessageAttachment } from '@/types'
import { useChatConverters } from './useChatConverters'

const runtimeState = { generation: 'gen-1' }

// Stable reference: the hook re-reconciles whenever the attachments array
// identity changes, mirroring the memoized call site in ChatWindow.
const NO_ATTACHMENTS: MessageAttachment[] = []

jest.mock('./useRuntime', () => ({
  useRuntime: () => ({ generation: runtimeState.generation }),
}))

describe('useChatConverters working-copy persistence', () => {
  it('keeps user-authored working text when the runtime generation changes', () => {
    const { result, rerender } = renderHook(
      ({ text }: { text: string }) => useChatConverters(text, NO_ATTACHMENTS),
      { initialProps: { text: 'original text' } },
    )

    act(() => {
      result.current.editInput('text', 'user edited text')
    })
    expect(result.current.workingInputs['text']).toBe('user edited text')

    runtimeState.generation = 'gen-2'
    rerender({ text: 'original text' })

    expect(result.current.workingInputs['text']).toBe('user edited text')
  })

  it('still clears generated stage results when the runtime generation changes', () => {
    const { result, rerender } = renderHook(
      ({ text }: { text: string }) => useChatConverters(text, NO_ATTACHMENTS),
      { initialProps: { text: 'original text' } },
    )

    act(() => {
      result.current.editInput('text', 'working copy')
    })

    runtimeState.generation = 'gen-2'
    rerender({ text: 'original text' })

    expect(result.current.stageResults['text']).toEqual([])
    expect(result.current.applied).toEqual({})
  })
})
