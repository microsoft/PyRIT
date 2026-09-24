import { useCallback, useMemo, useRef, useState } from 'react'

import { buildConverterInputs } from '@/components/Chat/converterTypes'
import { useRuntime } from '@/hooks/useRuntime'
import { convertersApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type {
  ChatConverterController,
  ConverterInputPiece,
  ConverterPipelineStage,
  ConverterPreviewResponse,
  ConverterPreviewStep,
  ConverterStageResult,
  MessageAttachment,
  PieceConversion,
} from '@/types'
import { generateClientId } from '@/utils/clientId'
import { fileToBase64 } from '@/utils/messageMapper'

interface VersionedInput extends ConverterInputPiece {
  revision: number
}

interface ConversionState {
  generation: string
  sourceInputs: ConverterInputPiece[]
  inputs: VersionedInput[]
  nextRevision: number
  workingInputs: Record<string, string>
  pipelines: Record<string, ConverterPipelineStage[]>
  pipelineRevisions: Record<string, number>
  stageResults: Record<string, ConverterStageResult[]>
  errors: Record<string, string>
  applied: Record<string, PieceConversion>
  runId: number
  isConverting: boolean
}

interface ConversionJob {
  input: VersionedInput
  pipeline: ConverterPipelineStage[]
  prefix: ConverterStageResult[]
  start: number
  value: string
  dataType: string
}

interface ConversionScope {
  pieceType?: string
  pieceId?: string
  afterStageId?: string
  includeIncomplete?: boolean
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
    workingInputs: omitPieces(state.workingInputs, changed),
    stageResults: omitPieces(state.stageResults, changed),
    errors: omitPieces(state.errors, changed),
    applied: omitPieces(state.applied, changed),
  }
}

function changePipeline(state: ConversionState, pieceType: string, stages: ConverterPipelineStage[]): ConversionState {
  const previous = state.pipelines[pieceType] ?? []
  let prefixLength = 0
  while (
    prefixLength < previous.length && prefixLength < stages.length
    && previous[prefixLength].id === stages[prefixLength].id
    && previous[prefixLength].converterId === stages[prefixLength].converterId
  ) prefixLength++
  if (prefixLength === previous.length && prefixLength === stages.length) return state

  const affected = new Set(state.inputs
    .filter((input: VersionedInput) => input.pieceType === pieceType)
    .map((input: VersionedInput) => input.id))
  const stageResults = { ...state.stageResults }
  for (const id of affected) stageResults[id] = (stageResults[id] ?? []).slice(0, prefixLength)
  return {
    ...state,
    pipelines: { ...state.pipelines, [pieceType]: stages },
    pipelineRevisions: {
      ...state.pipelineRevisions,
      [pieceType]: (state.pipelineRevisions[pieceType] ?? 0) + 1,
    },
    stageResults,
    errors: omitPieces(state.errors, affected),
    applied: omitPieces(state.applied, affected),
  }
}

function completedResults(state: ConversionState): Record<string, ConverterPreviewResponse> {
  const results: Record<string, ConverterPreviewResponse> = {}
  for (const input of state.inputs) {
    const pipeline = state.pipelines[input.pieceType] ?? []
    const stages = state.stageResults[input.id] ?? []
    const workingValue = state.workingInputs[input.id]
    if (stages.length === 0 && workingValue !== undefined && workingValue !== input.value) {
      results[input.id] = {
        original_value: input.value,
        original_value_data_type: input.dataType,
        converted_value: workingValue,
        converted_value_data_type: input.dataType,
        steps: [],
      }
      continue
    }
    const last = stages[stages.length - 1]
    if (!last || stages.length !== pipeline.length || stages.some(
      (stage: ConverterStageResult, index: number) => stage.stageId !== pipeline[index].id,
    )) continue
    results[input.id] = {
      original_value: input.value,
      original_value_data_type: input.dataType,
      converted_value: last.value,
      converted_value_data_type: last.generated.output_data_type,
      steps: stages.map((stage: ConverterStageResult) => stage.generated),
    }
  }
  return results
}

function invalidatePiece(state: ConversionState, pieceId: string): ConversionState {
  const revision = state.nextRevision + 1
  const ids = new Set([pieceId])
  return {
    ...state,
    nextRevision: revision,
    inputs: state.inputs.map((input: VersionedInput) => input.id === pieceId ? { ...input, revision } : input),
    errors: omitPieces(state.errors, ids),
    applied: omitPieces(state.applied, ids),
  }
}

export function useChatConverters(text: string, attachments: MessageAttachment[]): ChatConverterController {
  const { generation } = useRuntime()
  const inputs = useMemo(() => buildConverterInputs(text, attachments), [text, attachments])
  const [state, setState] = useState<ConversionState>(() => ({
    generation,
    sourceInputs: inputs,
    inputs: inputs.map((input: ConverterInputPiece) => ({ ...input, revision: 0 })),
    nextRevision: 0,
    workingInputs: {},
    pipelines: {},
    pipelineRevisions: {},
    stageResults: {},
    errors: {},
    applied: {},
    runId: 0,
    isConverting: false,
  }))
  const nextRunId = useRef(0)
  const activeRun = useRef<number | null>(null)

  if (state.generation !== generation) {
    activeRun.current = null
    setState({
      ...state,
      generation,
      sourceInputs: inputs,
      inputs: inputs.map((input: ConverterInputPiece) => ({ ...input, revision: state.nextRevision + 1 })),
      nextRevision: state.nextRevision + 1,
      workingInputs: {},
      stageResults: {},
      errors: {},
      applied: {},
      runId: state.runId + 1,
      isConverting: false,
    })
  } else if (state.sourceInputs !== inputs) {
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

  const editInput = useCallback((pieceId: string, value: string): void => {
    setState((current: ConversionState) => {
      const input = current.inputs.find((candidate: VersionedInput) => candidate.id === pieceId)
      if (!input || input.dataType !== 'text' || (current.workingInputs[pieceId] ?? input.value) === value) return current
      return {
        ...invalidatePiece(current, pieceId),
        workingInputs: { ...current.workingInputs, [pieceId]: value },
        stageResults: { ...current.stageResults, [pieceId]: [] },
      }
    })
  }, [])

  const editStageOutput = useCallback((pieceId: string, stageId: string, value: string): void => {
    setState((current: ConversionState) => {
      const stages = current.stageResults[pieceId] ?? []
      const index = stages.findIndex((stage: ConverterStageResult) => stage.stageId === stageId)
      const stage = stages[index]
      if (!stage || stage.generated.output_data_type !== 'text' || stage.value === value) return current
      return {
        ...invalidatePiece(current, pieceId),
        stageResults: {
          ...current.stageResults,
          [pieceId]: [...stages.slice(0, index), { ...stage, value }],
        },
      }
    })
  }, [])

  const runConversion = async ({
    pieceType,
    pieceId,
    afterStageId,
    includeIncomplete = false,
  }: ConversionScope): Promise<void> => {
    if (activeRun.current !== null) return
    const completed = completedResults(state)
    const selected = state.inputs.flatMap((input: VersionedInput): ConversionJob[] => {
      if (
        pieceType !== undefined
        && pieceType !== input.pieceType
        && (!includeIncomplete || completed[input.id] !== undefined)
      ) return []
      if (pieceId !== undefined && pieceId !== input.id) return []
      const pipeline = state.pipelines[input.pieceType] ?? []
      const previous = state.stageResults[input.id] ?? []
      const boundary = afterStageId === undefined ? -1 : previous.findIndex(
        (stage: ConverterStageResult) => stage.stageId === afterStageId,
      )
      if (afterStageId !== undefined && boundary < 0) return []
      const start = boundary + 1
      const value = boundary < 0 ? state.workingInputs[input.id] ?? input.value : previous[boundary].value
      const dataType = boundary < 0 ? input.dataType : previous[boundary].generated.output_data_type
      if (start >= pipeline.length || (boundary < 0 && !value.trim())) return []
      return [{ input, pipeline, prefix: previous.slice(0, start), start, value, dataType }]
    })
    if (selected.length === 0) return
    const runId = ++nextRunId.current
    activeRun.current = runId
    const affected = new Set(selected.map(({ input }: ConversionJob) => input.id))
    setState((current: ConversionState) => ({
      ...current,
      runId,
      isConverting: true,
      stageResults: {
        ...current.stageResults,
        ...Object.fromEntries(selected.map(({ input, prefix }: ConversionJob) => [input.id, prefix])),
      },
      errors: omitPieces(current.errors, affected),
      applied: omitPieces(current.applied, affected),
    }))

    const outcomes = await Promise.all(selected.map(async ({
      input, pipeline, prefix, start, value, dataType,
    }: ConversionJob) => {
      const pipelineRevision = state.pipelineRevisions[input.pieceType]
      try {
        const requestValue = start === 0 && input.file && state.workingInputs[input.id] === undefined
          ? `data:${input.file.type || 'application/octet-stream'};base64,${await fileToBase64(input.file)}`
          : value
        const remaining = pipeline.slice(start)
        const response = await convertersApi.previewConversion({
          original_value: requestValue,
          original_value_data_type: dataType,
          converter_ids: remaining.map((stage: ConverterPipelineStage) => stage.converterId),
        })
        if (response.steps.length !== remaining.length || response.steps.some(
          (step: ConverterPreviewStep, index: number) => step.converter_id !== remaining[index].converterId,
        )) throw new Error('The conversion response does not match the requested stages.')
        const stages: ConverterStageResult[] = response.steps.map((step: ConverterPreviewStep, index: number) => ({
          stageId: remaining[index].id, generated: step, value: step.output_value,
        }))
        return { input, pipelineRevision, stages: [...prefix, ...stages] }
      } catch (error) {
        return { input, pipelineRevision, error: `Conversion from stage ${start + 1} failed: ${toApiError(error).detail}` }
      }
    }))
    if (activeRun.current === runId) activeRun.current = null
    setState((current: ConversionState) => {
      if (current.runId !== runId) return current
      const stageResults = { ...current.stageResults }
      const errors = { ...current.errors }
      for (const outcome of outcomes) {
        const input = current.inputs.find((candidate: VersionedInput) => candidate.id === outcome.input.id)
        if (
          !input || input.revision !== outcome.input.revision
          || current.pipelineRevisions[input.pieceType] !== outcome.pipelineRevision
        ) continue
        if (outcome.stages) stageResults[input.id] = outcome.stages
        else if (outcome.error) errors[input.id] = outcome.error
      }
      return { ...current, stageResults, errors, isConverting: false }
    })
  }

  const apply = useCallback((): void => {
    setState((current: ConversionState) => {
      if (current.isConverting) return current
      const applied: Record<string, PieceConversion> = {}
      const results = completedResults(current)
      for (const input of current.inputs) {
        const result = results[input.id]
        if (!result) continue
        applied[input.id] = {
          pieceId: input.id,
          pieceType: input.pieceType,
          converterInstanceIds: result.steps.map((step: ConverterPreviewStep) => step.converter_id),
          originalValue: input.value,
          convertedValue: result.converted_value,
          convertedDataType: result.converted_value_data_type,
        }
      }
      return { ...current, applied }
    })
  }, [])

  const clear = useCallback((pieceId: string): void => {
    setState((current: ConversionState) => ({ ...current, applied: omitPieces(current.applied, new Set([pieceId])) }))
  }, [])

  const clearAll = useCallback((): void => {
    activeRun.current = null
    setState((current: ConversionState) => ({
      ...current, runId: 0, isConverting: false, workingInputs: {}, stageResults: {}, errors: {}, applied: {},
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
    activeRun.current = null
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
        )) next = changePipeline(next, pieceType, stages)
      }
      return {
        ...next, applied: { ...conversions }, workingInputs: {}, stageResults: {}, errors: {}, runId: 0, isConverting: false,
      }
    })
  }, [])

  return {
    editRevision: state.nextRevision,
    inputs: state.inputs,
    workingInputs: state.workingInputs,
    pipelines: state.pipelines,
    stageResults: state.stageResults,
    results: completedResults(state),
    errors: state.errors,
    applied: state.applied,
    isConverting: state.isConverting,
    addConverter,
    setPipeline,
    retainConverters,
    convert: (pieceType: string) => runConversion({ pieceType, includeIncomplete: true }),
    convertRemaining: (pieceId: string, stageId: string) => runConversion({ pieceId, afterStageId: stageId }),
    editInput,
    editStageOutput,
    apply,
    clear,
    clearAll,
    editConvertedValue,
    restore,
  }
}
