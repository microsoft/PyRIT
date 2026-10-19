/**
 * Regression tests for useChatConverters around runtime generation changes
 * (#2867): user-authored working text must survive a generation swap, stale
 * edits must not survive a composer-text swap, and generated results must
 * clear. Review pass by @romanlutz on PR #2873.
 */
import { act, render, renderHook, waitFor } from '@testing-library/react'
import { Suspense, startTransition } from 'react'

import { convertersApi } from '@/services/api'
import { useChatConverters, usePieceConverters } from './useChatConverters'
import type { ConverterPipelineStage, MessageAttachment, PieceConversionUpdates, SourceInstanceSpec } from '@/types'

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
  const privateSpec = {
    source_name: 'base64-default', source_hash: 'source-hash', params: { example: 3 },
  }

  it('sends aligned private recipes and preserves applied provenance after the pane closes', async () => {
    mockedPreview.mockResolvedValue({
      ...makePreviewResponse(),
      converted_value: 'private output', converted_value_data_type: 'text',
      steps: [{ ...makePreviewResponse().steps[0], output_value: 'private output', provenance: 'signed-evidence' }],
    })
    const { result } = renderHook(() => useChatConverters('original text', NO_ATTACHMENTS))
    act(() => result.current.setPipeline('text', () => [
      { id: 'first', converterId: 'base64-default', temporary: privateSpec },
    ]))
    await act(async () => {
      const updates = await result.current.convert('text')
      expect(updates?.text?.converterProvenance).toEqual(['signed-evidence'])
    })
    expect(mockedPreview.mock.calls[0][0].converter_specs).toEqual([privateSpec])
    expect(result.current.applied.text.converterProvenance).toEqual(['signed-evidence'])
    const snapshot = result.current.snapshot()
    act(() => result.current.clearAll())
    act(() => result.current.restoreSnapshot(snapshot, 'original text', NO_ATTACHMENTS))
    expect(result.current.pipelines.text[0].temporary).toEqual(privateSpec)
    expect(result.current.applied.text.converterProvenance).toEqual(['signed-evidence'])
    act(() => result.current.discardTemporarySettings?.())
    expect(result.current.pipelines.text).toEqual([{ id: 'first', converterId: 'base64-default' }])
    expect(result.current.applied.text.convertedValue).toBe('private output')
    expect(result.current.applied.text.converterProvenance).toEqual(['signed-evidence'])
    expect(result.current.stageResults.text).toEqual([])
  })

  it('discards private settings on runtime replacement and rejects stale preview results', async () => {
    let finish: (value: ReturnType<typeof makePreviewResponse>) => void = () => {}
    mockedPreview.mockImplementation(() => new Promise((resolve) => { finish = resolve }))
    const { result, rerender } = renderHook(() => useChatConverters('original text', NO_ATTACHMENTS))
    act(() => result.current.setPipeline('text', () => [
      { id: 'first', converterId: 'base64-default', temporary: privateSpec },
    ]))
    let conversion: Promise<PieceConversionUpdates | void> | undefined
    act(() => { conversion = result.current.convert('text') })
    runtime.generation = 'gen-2'
    rerender()
    await act(async () => { finish(makePreviewResponse()); await conversion })
    expect(result.current.pipelines.text).toEqual([{ id: 'first', converterId: 'base64-default' }])
    expect(result.current.stageResults.text).toBeUndefined()
    expect(result.current.isConverting).toBe(false)
  })

  it('keeps a new conversion active when a discarded request finishes', async () => {
    const responses: Array<(value: ReturnType<typeof makePreviewResponse>) => void> = []
    mockedPreview.mockImplementation(() => new Promise((resolve) => { responses.push(resolve) }))
    const { result } = renderHook(() => useChatConverters('original text', NO_ATTACHMENTS))
    act(() => result.current.setPipeline('text', () => [
      { id: 'first', converterId: 'base64-default', temporary: privateSpec },
    ]))
    let discarded: Promise<PieceConversionUpdates | void> | undefined
    act(() => { discarded = result.current.convert('text') })
    act(() => result.current.discardTemporarySettings?.())
    expect(result.current.isConverting).toBe(false)
    expect(result.current.pipelines.text[0].temporary).toBeUndefined()

    let current: Promise<PieceConversionUpdates | void> | undefined
    act(() => { current = result.current.convert('text') })
    await act(async () => { responses[0](makePreviewResponse()); await discarded })
    expect(result.current.stageResults.text).toEqual([])
    expect(result.current.isConverting).toBe(true)
    await act(async () => { responses[1](makePreviewResponse()); await current })
    expect(result.current.stageResults.text).toHaveLength(1)
    expect(result.current.isConverting).toBe(false)
  })

  it('changes only the selected duplicate stage and invalidates its downstream results', async () => {
    mockedPreview.mockResolvedValue({
      ...makePreviewResponse(), steps: [makePreviewResponse().steps[0], makePreviewResponse().steps[0]],
    })
    const { result } = renderHook(() => useChatConverters('original text', NO_ATTACHMENTS))
    act(() => result.current.setPipeline('text', () => [
      { id: 'first', converterId: 'base64-default' }, { id: 'second', converterId: 'base64-default' },
    ]))
    await act(async () => { await result.current.convert('text') })
    act(() => result.current.setPipeline('text', (stages) => stages.map((stage) =>
      stage.id === 'second' ? { ...stage, temporary: privateSpec } : stage)))
    expect(result.current.pipelines.text[0].temporary).toBeUndefined()
    expect(result.current.pipelines.text[1].temporary).toEqual(privateSpec)
    expect(result.current.stageResults.text).toHaveLength(1)
  })

  it('restores exact working inputs, stage IDs, middle edits, and applied values after a send clears them', async () => {
    mockedPreview.mockImplementation(async (request) => {
      let value = request.original_value
      const steps = request.converter_ids.map((converterId: string) => {
        const input = value
        value = `${input}|${converterId}`
        return {
          converter_id: converterId, converter_type: 'TestConverter',
          input_value: input, input_data_type: 'text', output_value: value, output_data_type: 'text',
        }
      })
      return { steps }
    })
    const { result, rerender } = renderHook(
      ({ text }: { text: string }) => useChatConverters(text, NO_ATTACHMENTS),
      { initialProps: { text: 'original text' } },
    )
    act(() => {
      result.current.setPipeline('text', () => [
        { id: 'first-stage', converterId: 'first' }, { id: 'second-stage', converterId: 'second' },
      ])
      result.current.editInput('text', 'working input')
    })
    await act(async () => { await result.current.convert('text') })
    act(() => { result.current.editStageOutput('text', 'first-stage', 'Edited middle') })
    await act(async () => { await result.current.convertRemaining('text', 'first-stage') })
    act(() => { result.current.editConvertedValue('text', 'Exact sendable result') })
    const saved = result.current.snapshot()
    act(() => { result.current.clearAll() })
    rerender({ text: '' })
    expect(result.current.stageResults).toEqual({})
    rerender({ text: 'original text' })
    act(() => { result.current.restoreSnapshot(saved, 'original text', NO_ATTACHMENTS) })
    expect(result.current.workingInputs).toEqual(saved.workingInputs)
    expect(result.current.pipelines).toEqual(saved.pipelines)
    expect(result.current.stageResults).toEqual(saved.stageResults)
    expect(result.current.stageResults.text[0].value).toBe('Edited middle')
    expect(result.current.applied.text.convertedValue).toBe('Exact sendable result')
    expect(result.current.repeatDisabledReason).toBeDefined()
    expect(mockedPreview).toHaveBeenCalledTimes(2)
    runtime.generation = 'replacement'
    rerender({ text: 'original text' })
    expect(() => result.current.restoreSnapshot(saved, 'original text', NO_ATTACHMENTS)).toThrow(/runtime changed/)
    expect(result.current.stageResults).toEqual({})
  })

  it('applies completed conversions and final-stage edits without a separate action', async () => {
    mockedPreview.mockResolvedValue(makePreviewResponse())
    const { result } = renderHook(() => useChatConverters('original text', NO_ATTACHMENTS))
    act(() => {
      result.current.setPipeline('text', () => [{ id: 'stage-1', converterId: 'base64-default' }])
    })
    await act(async () => { await result.current.convert('text') })
    expect(result.current.applied.text.convertedValue).toBe('b3JpZ2luYWwgdGV4dA==')
    expect(result.current.repeatDisabledReason).toBeUndefined()
    act(() => { result.current.editStageOutput('text', 'stage-1', 'Edited final output') })
    expect(result.current.applied.text.convertedValue).toBe('Edited final output')
    expect(result.current.repeatDisabledReason).toMatch(/editing a conversion/)
    act(() => { result.current.clearAll() })
    expect(result.current.repeatDisabledReason).toBeUndefined()
  })

  it('disables repetitions for partial-region conversion even after the markers are consumed', async () => {
    mockedPreview.mockResolvedValue({
      ...makePreviewResponse(),
      steps: [{ ...makePreviewResponse().steps[0], input_value: 'before \u27eapart\u27eb after' }],
    })
    const { result } = renderHook(() => useChatConverters('before part after', NO_ATTACHMENTS))
    act(() => {
      result.current.setPipeline('text', () => [{ id: 'stage-1', converterId: 'base64-default' }])
      result.current.editInput('text', 'before \u27eapart\u27eb after')
    })
    await act(async () => { await result.current.convert('text') })
    expect(result.current.applied.text).toBeDefined()
    expect(result.current.repeatDisabledReason).toMatch(/part of a prompt/)
  })

  it('disables repetitions after the sendable converted value is manually edited', async () => {
    mockedPreview.mockResolvedValue(makePreviewResponse())
    const { result } = renderHook(() => useChatConverters('original text', NO_ATTACHMENTS))
    act(() => {
      result.current.setPipeline('text', () => [{ id: 'stage-1', converterId: 'base64-default' }])
    })
    await act(async () => { await result.current.convert('text') })
    act(() => { result.current.editConvertedValue('text', 'Changed in composer') })
    expect(result.current.applied.text.convertedValue).toBe('Changed in composer')
    expect(result.current.repeatDisabledReason).toBeDefined()
  })

  it('restores repeat pipelines and exact applied values without retaining unrelated selections', () => {
    const { result } = renderHook(() => useChatConverters('original text', NO_ATTACHMENTS))
    const pipelines = {
      text: [
        { id: 'stage-1', converterId: 'first' },
        { id: 'stage-2', converterId: 'second' },
        { id: 'stage-3', converterId: 'first' },
      ],
    }
    const conversions = {
      text: {
        pieceId: 'text', pieceType: 'text', originalValue: 'original text', convertedValue: 'exact edited preview',
        convertedDataType: 'text', converterInstanceIds: ['first', 'second', 'first'],
      },
    }
    act(() => {
      result.current.addConverter('image', 'unrelated')
      result.current.restore('original text', NO_ATTACHMENTS, conversions, pipelines)
    })
    expect(result.current.pipelines.text).toEqual(pipelines.text)
    expect(result.current.pipelines.image).toEqual([])
    expect(result.current.applied).toEqual(conversions)
    expect(mockedPreview).not.toHaveBeenCalled()
  })

  it.each([
    { change: 'changed', initial: privateSpec, restored: { ...privateSpec, params: { example: 5 } } },
    { change: 'cleared', initial: privateSpec, restored: undefined },
    { change: 'added', initial: undefined, restored: privateSpec },
  ])('restores $change temporary settings with the same converter IDs', async ({
    initial, restored,
  }: { initial?: SourceInstanceSpec; restored?: SourceInstanceSpec }) => {
    const { result } = renderHook(() => useChatConverters('original text', NO_ATTACHMENTS))
    const pipeline: ConverterPipelineStage[] = [
      { id: 'first', converterId: 'base64-default' },
      { id: 'second', converterId: 'base64-default', temporary: initial },
    ]
    const restoredPipeline: ConverterPipelineStage[] = [
      pipeline[0], { ...pipeline[1], temporary: restored },
    ]
    act(() => result.current.setPipeline('text', () => pipeline))
    act(() => result.current.restore('original text', NO_ATTACHMENTS, {}, { text: restoredPipeline }))

    expect(result.current.pipelines.text).toEqual(restoredPipeline)
    mockedPreview.mockResolvedValue({
      ...makePreviewResponse(), steps: [makePreviewResponse().steps[0], makePreviewResponse().steps[0]],
    })
    await act(async () => { await result.current.convert('text') })
    const request = mockedPreview.mock.calls[0][0]
    expect(request.converter_ids).toEqual(['base64-default', 'base64-default'])
    expect(request.converter_specs).toEqual(restored ? [null, restored] : undefined)
  })

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

describe('useChatConverters concurrent conversion runs', () => {
  const attachments = [{
    draftId: 'att-1', type: 'image', name: 'photo.png', url: 'blob:photo',
    mimeType: 'image/png', size: 10, sourceValue: 'photo source value', sourceDataType: 'image_path',
  }]

  function setupTwoPieces() {
    return renderHook(
      ({ text }: { text: string }) => useChatConverters(text, attachments),
      { initialProps: { text: 'original text' } },
    )
  }

  function deferredPreview() {
    let resolve!: (value: unknown) => void
    const promise = new Promise((r) => { resolve = r })
    return {
      promise,
      respond: (value: string) => resolve({
        original_value: 'x',
        steps: [{
          converter_id: 'base64-default', converter_type: 'Base64Converter',
          input_value: 'x', input_data_type: 'text',
          output_value: value, output_data_type: 'text',
        }],
      }),
    }
  }

  it('keeps converting disabled until every concurrent run finishes', async () => {
    const baseOutput = (original_value: string) => (original_value.includes('photo') ? 'base-photo' : 'base-text')
    mockedPreview.mockImplementation(({ original_value }: { original_value: string }) => Promise.resolve({
      original_value,
      steps: [1, 2].map((step) => ({
        converter_id: 'base64-default', converter_type: 'Base64Converter',
        input_value: original_value, input_data_type: 'text',
        output_value: step === 2 ? baseOutput(original_value) : `stage-one-of-${baseOutput(original_value)}`,
        output_data_type: 'text',
      })),
    }))
    const { result } = setupTwoPieces()
    act(() => {
      result.current.setPipeline('text', (stages) => [
        ...stages, { id: 'stage-1', converterId: 'base64-default' }, { id: 'stage-2', converterId: 'base64-default' },
      ])
      result.current.setPipeline('image', (stages) => [
        ...stages, { id: 'stage-1', converterId: 'base64-default' }, { id: 'stage-2', converterId: 'base64-default' },
      ])
    })
    await act(async () => { result.current.convert('text') })
    await waitFor(() => {
      expect(result.current.stageResults['text']![1].value).toBe('base-text')
      expect(result.current.stageResults['att-1']![1].value).toBe('base-photo')
    })

    const textRun = deferredPreview()
    const imageRun = deferredPreview()
    mockedPreview.mockImplementation(({ original_value }: { original_value: string }) =>
      original_value.includes('photo') ? imageRun.promise : textRun.promise)

    // Two piece scoped reruns: the image one finishes first.
    let started: Promise<void> | undefined
    act(() => {
      started = Promise.all([
        result.current.convertRemaining('text', 'stage-1'),
        result.current.convertRemaining('att-1', 'stage-1'),
      ]).then(() => undefined)
    })
    await act(async () => { imageRun.respond('rerun-image') })
    await waitFor(() => {
      expect(result.current.stageResults['att-1']![1].value).toBe('rerun-image')
    })
    // The text rerun is still pending: its result is truncated to the kept prefix.
    expect(result.current.stageResults['text']!.length).toBe(1)
    expect(result.current.isConverting).toBe(true)
    act(() => { result.current.apply() })
    expect(result.current.applied['att-1']?.convertedValue).toBe('rerun-image')

    await act(async () => {
      textRun.respond('rerun-text')
      await started
    })
    await waitFor(() => {
      expect(result.current.stageResults['text']![1].value).toBe('rerun-text')
    })
    expect(result.current.isConverting).toBe(false)
    act(() => { result.current.apply() })
    expect(result.current.applied['text']).toBeDefined()
    expect(result.current.applied['att-1']).toBeDefined()
  })

  it('keeps the first run results when the first one finishes first', async () => {
    const baseOutput = (original_value: string) => (original_value.includes('photo') ? 'base-photo' : 'base-text')
    mockedPreview.mockImplementation(({ original_value }: { original_value: string }) => Promise.resolve({
      original_value,
      steps: [1, 2].map((step) => ({
        converter_id: 'base64-default', converter_type: 'Base64Converter',
        input_value: original_value, input_data_type: 'text',
        output_value: step === 2 ? baseOutput(original_value) : `stage-one-of-${baseOutput(original_value)}`,
        output_data_type: 'text',
      })),
    }))
    const { result } = setupTwoPieces()
    act(() => {
      result.current.setPipeline('text', (stages) => [
        ...stages, { id: 'stage-1', converterId: 'base64-default' }, { id: 'stage-2', converterId: 'base64-default' },
      ])
      result.current.setPipeline('image', (stages) => [
        ...stages, { id: 'stage-1', converterId: 'base64-default' }, { id: 'stage-2', converterId: 'base64-default' },
      ])
    })
    await act(async () => { result.current.convert('text') })
    await waitFor(() => {
      expect(result.current.stageResults['text']![1].value).toBe('base-text')
      expect(result.current.stageResults['att-1']![1].value).toBe('base-photo')
    })

    const textRun = deferredPreview()
    const imageRun = deferredPreview()
    mockedPreview.mockImplementation(({ original_value }: { original_value: string }) =>
      original_value.includes('photo') ? imageRun.promise : textRun.promise)

    let started: Promise<void> | undefined
    act(() => {
      started = Promise.all([
        result.current.convertRemaining('text', 'stage-1'),
        result.current.convertRemaining('att-1', 'stage-1'),
      ]).then(() => undefined)
    })
    await act(async () => { textRun.respond('rerun-text') })
    await waitFor(() => {
      expect(result.current.stageResults['text']![1].value).toBe('rerun-text')
    })
    expect(result.current.stageResults['att-1']!.length).toBe(1)
    expect(result.current.isConverting).toBe(true)

    await act(async () => {
      imageRun.respond('rerun-image')
      await started
    })
    await waitFor(() => {
      expect(result.current.stageResults['att-1']![1].value).toBe('rerun-image')
    })
    expect(result.current.isConverting).toBe(false)
  })

  it('keeps the first piece result when a second conversion starts in the same tick', async () => {
    let previewCall = 0
    mockedPreview.mockImplementation(({ original_value }: { original_value: string }) => {
      previewCall += 1
      return Promise.resolve({
        original_value,
        steps: [
          {
            converter_id: 'base64-default', converter_type: 'Base64Converter',
            input_value: original_value, input_data_type: 'text',
            output_value: `out-${previewCall}`, output_data_type: 'text',
          },
        ],
      })
    })
    const attachments = [{
      draftId: 'att-1', type: 'image', name: 'photo.png', url: 'blob:photo',
      mimeType: 'image/png', size: 10, sourceValue: 'photo source value', sourceDataType: 'image_path',
    }]
    const { result } = renderHook(
      ({ text }: { text: string }) => useChatConverters(text, attachments),
      { initialProps: { text: 'original text' } },
    )
    act(() => {
      result.current.setPipeline('text', (stages) => [...stages, { id: 'stage-1', converterId: 'base64-default' }])
      result.current.setPipeline('image', (stages) => [...stages, { id: 'stage-1', converterId: 'base64-default' }])
    })
    await act(async () => {
      result.current.convert('text')
    })
    await waitFor(() => {
      expect(result.current.stageResults['text']?.length).toBe(1)
      expect(result.current.stageResults['att-1']?.length).toBe(1)
    })
    const textBefore = result.current.stageResults['text']![0].value
    const attBefore = result.current.stageResults['att-1']![0].value
    expect(textBefore).not.toBe(attBefore)

    // Two piece scoped reruns started in one tick: each input keeps its own update.
    await act(async () => {
      void result.current.convert('text')
      void result.current.convert('image')
    })

    await waitFor(() => {
      expect(result.current.stageResults['text']![0].value).not.toBe(textBefore)
    })
    expect(result.current.stageResults['att-1']![0].value).not.toBe(attBefore)
    expect(result.current.isConverting).toBe(false)
  })
})

describe('usePieceConverters interrupted render', () => {
  it('keeps a pending conversion committed when a scope change render is abandoned', async () => {
    const inputs = [{
      id: 'text', pieceType: 'text', name: 'Text', dataType: 'text', value: 'original text',
    }]
    let resolveConversion!: (value: unknown) => void
    mockedPreview.mockImplementation(() => new Promise((resolve) => { resolveConversion = resolve }))
    let controller: ReturnType<typeof usePieceConverters> | undefined
    const capture = (next: ReturnType<typeof usePieceConverters>): void => { controller = next }
    const never = new Promise(() => {})

    function Harness({ scopeKey, suspend }: { scopeKey: string; suspend: boolean }) {
      const current = usePieceConverters(inputs, scopeKey)
      capture(current)
      if (suspend && scopeKey === 'B') throw never
      return null
    }

    const { rerender } = render(
      <Suspense fallback={<div />}><Harness scopeKey="A" suspend={false} /></Suspense>
    )
    act(() => {
      controller!.setPipeline('text', (stages) => [...stages, { id: 'stage-1', converterId: 'base64-default' }])
    })
    act(() => {
      void controller!.convert('text')
    })
    expect(controller!.isConverting).toBe(true)

    // Switch scope inside a transition whose render suspends and never commits.
    act(() => {
      startTransition(() => {
        rerender(<Suspense fallback={<div />}><Harness scopeKey="B" suspend /></Suspense>)
      })
    })

    // The abandoned render must not have invalidated the pending conversion.
    await act(async () => {
      resolveConversion({
        original_value: 'original text',
        steps: [{
          converter_id: 'base64-default', converter_type: 'Base64Converter',
          input_value: 'original text', input_data_type: 'text',
          output_value: 'committed-value', output_data_type: 'text',
        }],
      })
    })
    await waitFor(() => {
      expect(controller!.stageResults['text']![0].value).toBe('committed-value')
    })
    expect(controller!.isConverting).toBe(false)
  })
})
