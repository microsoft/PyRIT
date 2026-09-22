import { useCallback, useMemo, useRef, useState } from 'react'

import { buildConverterInputs } from '@/components/Chat/converterTypes'
import { convertersApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type {
  ChatConverterController,
  ConverterInputPiece,
  ConverterPipelineStage,
  ConverterPreviewResponse,
  MessageAttachment,
  PieceConversion,
} from '@/types'
import { generateClientId } from '@/utils/clientId'
import { fileToBase64 } from '@/utils/messageMapper'

interface VersionedInput extends ConverterInputPiece {
  revision: number
}

interface ConversionState {
  sourceInputs: ConverterInputPiece[]
  inputs: VersionedInput[]
  nextRevision: number
  pipelines: Record<string, ConverterPipelineStage[]>
  pipelineRevisions: Record<string, number>
  results: Record<string, ConverterPreviewResponse>
  errors: Record<string, string>
  applied: Record<string, PieceConversion>
  runId: number
  isConverting: boolean
}

function omitPieces<T>(values: Record<string, T>, ids: Set<string>): Record<string, T> {
  return Object.fromEntries(Object.entries(values).filter(([id]: [string, T]) => !ids.has(id)))
}

function reconcileInputs(state: ConversionState, inputs: ConverterInputPiece[]): ConversionState {
  let nextRevision = state.nextRevision
  const changed = new Set(state.inputs.map((input: VersionedInput) => input.id))
  const versionedInputs = inputs.map((input: ConverterInputPiece): VersionedInput => {
    const previous = state.inputs.find((candidate: VersionedInput) => candidate.id === input.id)
    if (
      previous && previous.value === input.value && previous.dataType === input.dataType
      && previous.pieceType === input.pieceType && previous.file === input.file
    ) {
      changed.delete(input.id)
      return { ...input, revision: previous.revision }
    }
    return { ...input, revision: ++nextRevision }
  })
  return {
    ...state,
    sourceInputs: inputs,
    inputs: versionedInputs,
    nextRevision,
    results: omitPieces(state.results, changed),
    errors: omitPieces(state.errors, changed),
    applied: omitPieces(state.applied, changed),
  }
}

function changePipeline(state: ConversionState, pieceType: string, stages: ConverterPipelineStage[]): ConversionState {
  const previous = state.pipelines[pieceType] ?? []
  if (previous.length === stages.length && previous.every(
    (stage: ConverterPipelineStage, index: number) => stage === stages[index],
  )) {
    return state
  }
  const affected = new Set(state.inputs
    .filter((input: VersionedInput) => input.pieceType === pieceType)
    .map((input: VersionedInput) => input.id))
  return {
    ...state,
    pipelines: { ...state.pipelines, [pieceType]: stages },
    pipelineRevisions: {
      ...state.pipelineRevisions,
      [pieceType]: (state.pipelineRevisions[pieceType] ?? 0) + 1,
    },
    results: omitPieces(state.results, affected),
    errors: omitPieces(state.errors, affected),
    applied: omitPieces(state.applied, affected),
  }
}

function makeConversion(input: ConverterInputPiece, ids: string[], response: ConverterPreviewResponse): PieceConversion {
  return {
    pieceId: input.id,
    pieceType: input.pieceType,
    converterInstanceIds: [...ids],
    originalValue: input.value,
    convertedValue: response.converted_value,
    convertedDataType: response.converted_value_data_type,
  }
}

export function useChatConverters(text: string, attachments: MessageAttachment[]): ChatConverterController {
  const inputs = useMemo(() => buildConverterInputs(text, attachments), [text, attachments])
  const [state, setState] = useState<ConversionState>(() => ({
    sourceInputs: inputs,
    inputs: inputs.map((input: ConverterInputPiece) => ({ ...input, revision: 0 })),
    nextRevision: 0,
    pipelines: {},
    pipelineRevisions: {},
    results: {},
    errors: {},
    applied: {},
    runId: 0,
    isConverting: false,
  }))
  const nextRunId = useRef(0)

  if (state.sourceInputs !== inputs) {
    setState(reconcileInputs(state, inputs))
  }

  const setPipeline = useCallback((
    pieceType: string,
    update: (stages: ConverterPipelineStage[]) => ConverterPipelineStage[],
  ): void => {
    setState((current: ConversionState) => changePipeline(current, pieceType, update(current.pipelines[pieceType] ?? [])))
  }, [])

  const addConverter = useCallback((pieceType: string, converterId: string): void => {
    const stage: ConverterPipelineStage = { id: generateClientId(), converterId }
    setPipeline(pieceType, (stages: ConverterPipelineStage[]) => [...stages, stage])
  }, [setPipeline])

  const retainConverters = useCallback((availableIds: Set<string>): void => {
    setState((current: ConversionState) => {
      let next = current
      for (const [pieceType, stages] of Object.entries(current.pipelines)) {
        next = changePipeline(next, pieceType, stages.filter(
          (stage: ConverterPipelineStage) => availableIds.has(stage.converterId),
        ))
      }
      return next
    })
  }, [])

  const convert = async (): Promise<void> => {
    if (state.isConverting) return
    const selectedInputs = state.inputs.filter((input: VersionedInput) => (
      input.value.trim() && state.pipelines[input.pieceType]?.length
    ))
    if (selectedInputs.length === 0) return
    const runId = ++nextRunId.current
    setState((current: ConversionState) => ({
      ...current, runId, isConverting: true, results: {}, errors: {}, applied: {},
    }))

    const outcomes = await Promise.all(selectedInputs.map(async (input: VersionedInput) => {
      const converterIds = state.pipelines[input.pieceType].map((stage: ConverterPipelineStage) => stage.converterId)
      const pipelineRevision = state.pipelineRevisions[input.pieceType]
      try {
        const value = input.file
          ? `data:${input.file.type || 'application/octet-stream'};base64,${await fileToBase64(input.file)}`
          : input.value
        const response = await convertersApi.previewConversion({
          original_value: value,
          original_value_data_type: input.dataType,
          converter_ids: converterIds,
        })
        return { input, pipelineRevision, response }
      } catch (error) {
        return { input, pipelineRevision, error: toApiError(error).detail }
      }
    }))

    setState((current: ConversionState) => {
      if (current.runId !== runId) return current
      const results: Record<string, ConverterPreviewResponse> = {}
      const errors: Record<string, string> = {}
      for (const outcome of outcomes) {
        const input = current.inputs.find((candidate: VersionedInput) => candidate.id === outcome.input.id)
        if (
          !input || input.revision !== outcome.input.revision
          || current.pipelineRevisions[input.pieceType] !== outcome.pipelineRevision
        ) continue
        if (outcome.response) results[input.id] = outcome.response
        else if (outcome.error) errors[input.id] = outcome.error
      }
      return { ...current, results, errors, isConverting: false }
    })
  }

  const apply = useCallback((): void => {
    setState((current: ConversionState) => {
      if (current.isConverting) return current
      const applied: Record<string, PieceConversion> = {}
      for (const input of current.inputs) {
        const result = current.results[input.id]
        if (result) {
          const converterIds = current.pipelines[input.pieceType].map((stage: ConverterPipelineStage) => stage.converterId)
          applied[input.id] = makeConversion(input, converterIds, result)
        }
      }
      return { ...current, applied }
    })
  }, [])

  const clear = useCallback((pieceId: string): void => {
    setState((current: ConversionState) => ({ ...current, applied: omitPieces(current.applied, new Set([pieceId])) }))
  }, [])

  const clearAll = useCallback((): void => {
    setState((current: ConversionState) => ({
      ...current, runId: 0, isConverting: false, results: {}, errors: {}, applied: {},
    }))
  }, [])

  const editConvertedValue = useCallback((pieceId: string, value: string): void => {
    setState((current: ConversionState) => {
      const conversion = current.applied[pieceId]
      if (!conversion) return current
      return { ...current, applied: { ...current.applied, [pieceId]: { ...conversion, convertedValue: value } } }
    })
  }, [])

  const restore = useCallback((
    restoredText: string,
    restoredAttachments: MessageAttachment[],
    conversions: Record<string, PieceConversion>,
  ): void => {
    const restoredPipelines: Record<string, ConverterPipelineStage[]> = {}
    for (const conversion of Object.values(conversions)) {
      restoredPipelines[conversion.pieceType] = conversion.converterInstanceIds.map((converterId: string) => ({
        id: generateClientId(), converterId,
      }))
    }
    setState((current: ConversionState) => {
      let next = reconcileInputs(current, buildConverterInputs(restoredText, restoredAttachments))
      for (const [pieceType, stages] of Object.entries(restoredPipelines)) {
        const previous = next.pipelines[pieceType] ?? []
        if (previous.length !== stages.length || previous.some(
          (stage: ConverterPipelineStage, index: number) => stage.converterId !== stages[index].converterId,
        )) {
          next = changePipeline(next, pieceType, stages)
        }
      }
      return {
        ...next, applied: { ...conversions }, results: {}, errors: {}, runId: 0, isConverting: false,
      }
    })
  }, [])

  return {
    inputs: state.inputs,
    pipelines: state.pipelines,
    results: state.results,
    errors: state.errors,
    applied: state.applied,
    isConverting: state.isConverting,
    addConverter,
    setPipeline,
    retainConverters,
    convert,
    apply,
    clear,
    clearAll,
    editConvertedValue,
    restore,
  }
}
