import { act, renderHook, waitFor } from '@testing-library/react'

import { convertersApi } from '@/services/api'
import type { ConverterInputPiece, ConverterPreviewResponse } from '@/types'
import { buildAppliedConversions } from '@/utils/conversionResults'

import { usePieceConverters } from './useChatConverters'

jest.mock('@/services/api', () => ({
  convertersApi: { previewConversion: jest.fn() },
}))

function response(value: string): ConverterPreviewResponse {
  return {
    original_value: 'input', original_value_data_type: 'text',
    converted_value: value, converted_value_data_type: 'text',
    steps: [{
      converter_id: 'converter', converter_type: 'TestConverter', input_value: 'input', input_data_type: 'text',
      output_value: value, output_data_type: 'text',
    }],
  }
}

const first: ConverterInputPiece[] = [{ id: 'one', pieceType: 'text', name: 'First', dataType: 'text', value: 'first' }]
const second: ConverterInputPiece[] = [{ id: 'two', pieceType: 'text', name: 'Second', dataType: 'text', value: 'second' }]

describe('usePieceConverters', () => {
  beforeEach(() => jest.resetAllMocks())

  it('starts the new selection immediately and ignores a late result from the old selection', async () => {
    let resolveOld: (value: ConverterPreviewResponse) => void = () => {}
    jest.mocked(convertersApi.previewConversion)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve }))
      .mockResolvedValueOnce(response('new result'))
    const { result, rerender } = renderHook(
      ({ inputs, scope }: { inputs: ConverterInputPiece[]; scope: string }) => usePieceConverters(inputs, scope),
      { initialProps: { inputs: first, scope: 'first-message' } },
    )
    act(() => result.current.addConverter('text', 'converter'))
    let pending: Promise<void>
    act(() => { pending = result.current.convert('text') })
    await waitFor(() => expect(convertersApi.previewConversion).toHaveBeenCalledTimes(1))
    rerender({ inputs: second, scope: 'second-message' })
    await act(async () => { await result.current.convert('text') })
    expect(convertersApi.previewConversion).toHaveBeenCalledTimes(2)
    expect(result.current.results.two.converted_value).toBe('new result')
    await act(async () => { resolveOld(response('stale result')); await pending })
    expect(result.current.results.one).toBeUndefined()
    expect(result.current.results.two.converted_value).toBe('new result')
  })

  it('applies only successful selected pieces after a partial failure', async () => {
    jest.mocked(convertersApi.previewConversion)
      .mockRejectedValueOnce(new Error('First conversion failed'))
      .mockResolvedValueOnce(response('second result'))
    const inputs = [...first, ...second]
    const { result } = renderHook(() => usePieceConverters(inputs, 'both'))
    act(() => result.current.addConverter('text', 'converter'))
    await act(async () => { await result.current.convert('text') })
    expect(result.current.errors.one).toContain('First conversion failed')
    act(() => result.current.apply())
    expect(Object.keys(result.current.applied)).toEqual(['two'])
    expect(result.current.applied.two.convertedValue).toBe('second result')
  })

  it('converts an uploaded file when its persisted path is still empty', async () => {
    jest.mocked(convertersApi.previewConversion).mockResolvedValue(response('file result'))
    const inputs: ConverterInputPiece[] = [{
      id: 'file', pieceType: 'file', name: 'sample.txt', dataType: 'binary_path', value: '',
      file: new File(['file bytes'], 'sample.txt', { type: 'text/plain' }),
    }]
    const { result } = renderHook(() => usePieceConverters(inputs, 'file-message'))
    act(() => result.current.addConverter('file', 'converter'))
    await act(async () => { await result.current.convert('file') })
    expect(convertersApi.previewConversion).toHaveBeenCalledWith({
      original_value: `data:text/plain;base64,${btoa('file bytes')}`,
      original_value_data_type: 'binary_path', converter_ids: ['converter'],
    })
    expect(result.current.results.file.converted_value).toBe('file result')
  })

  it('retains stage IDs after middle edits and applies the same values in chat and the editor', async () => {
    const initial = response('last')
    initial.steps = ['first-stage', 'second-stage'].map((converter_id: string) => ({
      converter_id, converter_type: 'TestConverter', input_value: 'input', input_data_type: 'text',
      output_value: converter_id === 'first-stage' ? 'first' : 'last', output_data_type: 'text',
    }))
    const suffix = response('rerun')
    suffix.steps[0].converter_id = 'second-stage'
    jest.mocked(convertersApi.previewConversion).mockResolvedValueOnce(initial).mockResolvedValueOnce(suffix)
    const { result } = renderHook(() => usePieceConverters(first, 'message'))
    act(() => {
      result.current.addConverter('text', 'first-stage')
      result.current.addConverter('text', 'second-stage')
    })
    await act(async () => { await result.current.convert('text') })
    const stageId = result.current.stageResults.one[0].stageId
    act(() => { result.current.editStageOutput('one', stageId, 'manual middle') })
    expect(result.current.stageResults.one).toHaveLength(1)
    expect(result.current.results.one).toBeUndefined()
    await act(async () => { await result.current.convertRemaining('one', stageId) })
    expect(convertersApi.previewConversion).toHaveBeenLastCalledWith({
      original_value: 'manual middle', original_value_data_type: 'text', converter_ids: ['second-stage'],
    })
    const finalStage = result.current.stageResults.one[1].stageId
    act(() => { result.current.editStageOutput('one', finalStage, 'manual final') })
    const editorApplied = buildAppliedConversions(result.current.inputs, result.current.results)
    act(() => { result.current.apply() })
    expect(result.current.applied).toEqual(editorApplied)
    expect(editorApplied.one).toMatchObject({
      originalValue: 'first', convertedValue: 'manual final', converterInstanceIds: ['first-stage', 'second-stage'],
    })
    expect(convertersApi.previewConversion).toHaveBeenCalledTimes(2)
  })
})
