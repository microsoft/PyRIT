/**
 * Regression tests for useChatConverters around runtime generation changes
 * (#2867): user-authored working text must survive a generation swap, stale
 * edits must not survive a composer-text swap, and generated results must
 * clear. Review pass by @romanlutz on PR #2873.
 */
import { act, renderHook, waitFor } from '@testing-library/react'

import { convertersApi } from '@/services/api'
import { useChatConverters } from './useChatConverters'
import type { MessageAttachment } from '@/types'

const runtime = { generation: 'gen-1' }

jest.mock('@/services/api', () => ({
  convertersApi: {
    previewConversion: jest.fn(),
  },
}))

jest.mock('./useRuntime', () => ({
  useRuntime: () => ({ generation: runtime.generation }),
}))

// Stable reference: the hook re-reconciles whenever the attachments array
// identity changes, mirroring the memoized call site in ChatWindow.
const NO_ATTACHMENTS: MessageAttachment[] = []

const mockedPreview = convertersApi.previewConversion as jest.Mock

beforeEach(() => {
  runtime.generation = 'gen-1'
  mockedPreview.mockReset()
})

function makePreviewResponse() {
  return {
    original_value: 'original text',
    steps: [
      {
        converter_id: 'base64-default',
        converter_type: 'Base64Converter',
        input_value: 'original text',
        input_data_type: 'text',
        output_value: 'b3JpZ2luYWwgdGV4dA==',
        output_data_type: 'text',
      },
    ],
  }
}

describe('useChatConverters across runtime generation changes', () => {
  it('keeps user-authored working text when only the generation changes', () => {
    const { result, rerender } = renderHook(
      ({ text }: { text: string }) => useChatConverters(text, NO_ATTACHMENTS),
      { initialProps: { text: 'original text' } },
    )

    act(() => {
      result.current.editInput('text', 'user edited text')
    })
    expect(result.current.workingInputs['text']).toBe('user edited text')

    runtime.generation = 'gen-2'
    rerender({ text: 'original text' })

    expect(result.current.workingInputs['text']).toBe('user edited text')
  })

  it('drops the stale working edit when the composer text changes with the generation', () => {
    const { result, rerender } = renderHook(
      ({ text }: { text: string }) => useChatConverters(text, NO_ATTACHMENTS),
      { initialProps: { text: 'first draft' } },
    )

    act(() => {
      result.current.editInput('text', 'edited first draft')
    })
    expect(result.current.workingInputs['text']).toBe('edited first draft')

    runtime.generation = 'gen-2'
    rerender({ text: 'second draft' })

    expect(result.current.workingInputs['text']).toBeUndefined()
  })

  it('clears a real generated stage result on a generation change', async () => {
    mockedPreview.mockResolvedValue(makePreviewResponse())
    const { result, rerender } = renderHook(
      ({ text }: { text: string }) => useChatConverters(text, NO_ATTACHMENTS),
      { initialProps: { text: 'original text' } },
    )

    act(() => {
      result.current.setPipeline('text', (stages) => [...stages, { id: 'stage-1', converterId: 'base64-default' }])
    })
    await act(async () => {
      result.current.convert('text')
    })
    await waitFor(() => {
      expect(result.current.stageResults['text']?.length).toBe(1)
    })

    act(() => {
      result.current.apply()
    })
    expect(result.current.applied['text']).toBeDefined()

    runtime.generation = 'gen-2'
    rerender({ text: 'original text' })

    expect(result.current.stageResults['text']).toBeUndefined()
    expect(result.current.applied['text']).toBeUndefined()
  })

  it('keeps a configured-pipeline working edit across a generation change while clearing its generated result', async () => {
    mockedPreview.mockResolvedValue(makePreviewResponse())
    const { result, rerender } = renderHook(
      ({ text }: { text: string }) => useChatConverters(text, NO_ATTACHMENTS),
      { initialProps: { text: 'original text' } },
    )

    // Configure a pipeline, hand-edit the working copy on top of it, then run
    // the pipeline so a REAL generated stage result exists before the swap —
    // the second reproduction path from #2867.
    act(() => {
      result.current.setPipeline('text', (stages) => [...stages, { id: 'stage-1', converterId: 'base64-default' }])
    })
    act(() => {
      result.current.editInput('text', 'hand-edited working text')
    })
    expect(result.current.workingInputs['text']).toBe('hand-edited working text')

    await act(async () => {
      result.current.convert('text')
    })
    await waitFor(() => {
      expect(result.current.stageResults['text']?.length).toBe(1)
    })

    runtime.generation = 'gen-2'
    rerender({ text: 'original text' })

    expect(result.current.workingInputs['text']).toBe('hand-edited working text')
    expect(result.current.stageResults['text']).toBeUndefined()
  })
})
