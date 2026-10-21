import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'

import { buildAppliedConversions } from '@/utils/conversionResults'
import { buildConverterInputs } from '@/components/Chat/converterTypes'
import { useRuntime } from '@/hooks/useRuntime'
import { convertersApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type {
  ChatConverterController,
  ChatConverterSnapshot,
  ConverterInputPiece,
  ConverterPipelineStage,
  ConverterPreviewResponse,
  ConverterPreviewStep,
  ConverterStageResult,
  MessageAttachment,
  PieceConversion,
  PieceConversionUpdates,
} from '@/types'
import { generateClientId } from '@/utils/clientId'
import { fileToBase64 } from '@/utils/messageMapper'

interface VersionedInput extends ConverterInputPiece {
  revision: number
}

interface ConversionState {
  scopeKey?: string
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
  runOwners: Record<string, number>
  activeRuns: number[]
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

interface PieceConverterOptions {
  initialSnapshot?: ChatConverterSnapshot
  selectedIds?: ReadonlySet<string>
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
    && previous[prefixLength].temporary === stages[prefixLength].temporary
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
  const inputs = useMemo(() => buildConverterInputs(text, attachments), [text, attachments])
  return usePieceConverters(inputs)
}

export function usePieceConverters(
  inputs: ConverterInputPiece[], scopeKey?: string, options: PieceConverterOptions = {},
): ChatConverterController {
  const { generation } = useRuntime()
  const [state, setState] = useState<ConversionState>(() => ({
    generation,
    sourceInputs: inputs,
    scopeKey,
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
    runOwners: {},
    activeRuns: [],
    ...(options.initialSnapshot?.generation === generation ? options.initialSnapshot : {}),
  }))
  const nextRunId = useRef(0)
  const currentState = useRef<ConversionState | null>(state)
  useLayoutEffect(() => { currentState.current = state }, [state])
  useEffect(() => () => { currentState.current = null }, [])

  if (state.scopeKey !== scopeKey) {
    setState({
      ...reconcileInputs(state, inputs), scopeKey, generation, stageResults: {}, errors: {}, applied: {},
      pipelines: Object.fromEntries(Object.entries(state.pipelines).map(([pieceType, stages]) => [
        pieceType, stages.map((stage: ConverterPipelineStage) => ({ id: stage.id, converterId: stage.converterId })),
      ])),
      workingInputs: {}, runId: state.runId + 1, isConverting: false,
      runOwners: {}, activeRuns: [],
    })
  } else if (state.generation !== generation) {
    // A runtime generation change invalidates every generated result. Working
    // edits follow the same rule reconcileInputs applies: they survive only
    // when their underlying piece is unchanged, so a stale edit can never be
    // applied to text the user swapped in with the new generation.
    const next = reconcileInputs(state, inputs)
    setState({
      ...next,
      generation,
      pipelines: Object.fromEntries(Object.entries(next.pipelines).map(([pieceType, stages]) => [
        pieceType, stages.map((stage: ConverterPipelineStage) => ({ id: stage.id, converterId: stage.converterId })),
      ])),
      stageResults: {},
      errors: {},
      applied: {},
      runId: state.runId + 1,
      isConverting: false,
      runOwners: {},
      activeRuns: [],
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

  const discardTemporarySettings = useCallback((): void => {
    setState((current: ConversionState) => {
      let next = current
      for (const [pieceType, stages] of Object.entries(current.pipelines)) {
        next = changePipeline(next, pieceType, stages.map((stage: ConverterPipelineStage) => ({
          id: stage.id, converterId: stage.converterId,
        })))
      }
      return {
        ...next, applied: current.applied, runId: current.runId + 1, isConverting: false,
        runOwners: {}, activeRuns: [],
      }
    })
  }, [])

  const editInput = useCallback((pieceId: string, value: string): PieceConversionUpdates | void => {
    const update = (current: ConversionState): ConversionState => {
      const input = current.inputs.find((candidate: VersionedInput) => candidate.id === pieceId)
      if (!input || input.dataType !== 'text' || (current.workingInputs[pieceId] ?? input.value) === value) return current
      const next = {
        ...invalidatePiece(current, pieceId),
        workingInputs: { ...current.workingInputs, [pieceId]: value },
        stageResults: { ...current.stageResults, [pieceId]: [] },
      }
      return {
        ...next, applied: {
          ...next.applied,
          ...buildAppliedConversions(next.inputs.filter((piece: VersionedInput) => piece.id === pieceId), completedResults(next)),
        },
      }
    }
    if (!currentState.current) return
    const next = update(currentState.current)
    if (next === currentState.current) return
    setState(update)
    return { [pieceId]: next.applied[pieceId] ?? null }
  }, [])

  const editStageOutput = useCallback((
    pieceId: string, stageId: string, value: string,
  ): PieceConversionUpdates | void => {
    const update = (current: ConversionState): ConversionState => {
      const stages = current.stageResults[pieceId] ?? []
      const index = stages.findIndex((stage: ConverterStageResult) => stage.stageId === stageId)
      const stage = stages[index]
      if (!stage || stage.generated.output_data_type !== 'text' || stage.value === value) return current
      const next = {
        ...invalidatePiece(current, pieceId),
        stageResults: {
          ...current.stageResults,
          [pieceId]: [...stages.slice(0, index), { ...stage, value }],
        },
      }
      return {
        ...next,
        applied: {
          ...next.applied,
          ...buildAppliedConversions(next.inputs.filter((input: VersionedInput) => input.id === pieceId), completedResults(next)),
        },
      }
    }
    if (!currentState.current) return
    const next = update(currentState.current)
    if (next === currentState.current) return
    setState(update)
    return { [pieceId]: next.applied[pieceId] ?? null }
  }, [])

  const runConversion = async ({
    pieceType,
    pieceId,
    afterStageId,
    includeIncomplete = false,
  }: ConversionScope): Promise<PieceConversionUpdates | void> => {
    if (state.activeRuns.length > 0 && state.activeRuns[state.activeRuns.length - 1] === state.runId) return
    const completed = completedResults(state)
    const selected = state.inputs.flatMap((input: VersionedInput): ConversionJob[] => {
      if (options.selectedIds && !options.selectedIds.has(input.id)) return []
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
      if (start >= pipeline.length || (boundary < 0 && !value.trim() && !input.file)) return []
      return [{ input, pipeline, prefix: previous.slice(0, start), start, value, dataType }]
    })
    if (selected.length === 0) return
    const runId = ++nextRunId.current
    const affected = new Set(selected.map(({ input }: ConversionJob) => input.id))
    const startRun = (current: ConversionState): ConversionState => ({
      ...current,
      runId,
      isConverting: true,
      activeRuns: [...current.activeRuns, runId],
      runOwners: {
        ...current.runOwners,
        ...Object.fromEntries(selected.map(({ input }: ConversionJob) => [input.id, runId])),
      },
      stageResults: {
        ...current.stageResults,
        ...Object.fromEntries(selected.map(({ input, prefix }: ConversionJob) => [input.id, prefix])),
      },
      errors: omitPieces(current.errors, affected),
      applied: omitPieces(current.applied, affected),
    })
    if (!currentState.current) return
    currentState.current = startRun(currentState.current)
    setState(startRun)

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
          ...(remaining.some((stage: ConverterPipelineStage) => stage.temporary) ? {
            converter_specs: remaining.map((stage: ConverterPipelineStage) => stage.temporary ?? null),
          } : {}),
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
    const completeRun = (current: ConversionState): {
      state: ConversionState; updates: PieceConversionUpdates
    } => {
      const activeRuns = current.activeRuns.filter((active: number) => active !== runId)
      const stageResults = { ...current.stageResults }
      const errors = { ...current.errors }
      const accepted = new Set<string>()
      const updates: PieceConversionUpdates = {}
      for (const outcome of outcomes) {
        // A newer run that selected this input owns its outcome slot now.
        if (current.runOwners[outcome.input.id] !== runId) continue
        const input = current.inputs.find((candidate: VersionedInput) => candidate.id === outcome.input.id)
        if (
          !input || input.revision !== outcome.input.revision
          || current.pipelineRevisions[input.pieceType] !== outcome.pipelineRevision
        ) continue
        updates[input.id] = null
        if (outcome.stages) {
          stageResults[input.id] = outcome.stages
          accepted.add(input.id)
        }
        else if (outcome.error) errors[input.id] = outcome.error
      }
      const next = { ...current, stageResults, errors, activeRuns, isConverting: activeRuns.length > 0 }
      const applied = buildAppliedConversions(
        next.inputs.filter((input: VersionedInput) => accepted.has(input.id)), completedResults(next),
      )
      return {
        state: { ...next, applied: { ...next.applied, ...applied } },
        updates: { ...updates, ...applied },
      }
    }
    if (!currentState.current) return
    const completion = completeRun(currentState.current)
    currentState.current = completion.state
    setState((current: ConversionState) => completeRun(current).state)
    return Object.keys(completion.updates).length ? completion.updates : undefined
  }

  const apply = useCallback((): void => {
    setState((current: ConversionState) => {
      if (current.isConverting) return current
      const applied = buildAppliedConversions(current.inputs, completedResults(current))
      return { ...current, applied }
    })
  }, [])

  const clear = useCallback((pieceId: string): void => {
    setState((current: ConversionState) => ({ ...current, applied: omitPieces(current.applied, new Set([pieceId])) }))
  }, [])

  const clearAll = useCallback((): void => {
    setState((current: ConversionState) => ({
      ...current, runId: 0, isConverting: false, workingInputs: {}, stageResults: {}, errors: {}, applied: {},
      runOwners: {}, activeRuns: [],
    }))
  }, [])

  const editConvertedValue = useCallback((pieceId: string, value: string): void => {
    setState((current: ConversionState) => {
      const conversion = current.applied[pieceId]
      if (!conversion) return current
      return {
        ...current,
        applied: { ...current.applied, [pieceId]: { ...conversion, convertedValue: value, manuallyEdited: true } },
      }
    })
  }, [])

  const restore = useCallback((
    restoredText: string,
    restoredAttachments: MessageAttachment[],
    conversions: Record<string, PieceConversion>,
    pipelines?: Record<string, ConverterPipelineStage[]>,
  ): void => {
    const restoredPipelines: Record<string, ConverterPipelineStage[]> = { ...pipelines }
    if (!pipelines) {
      for (const conversion of Object.values(conversions)) {
        restoredPipelines[conversion.pieceType] = conversion.converterInstanceIds.map((converterId: string) => ({
          id: generateClientId(), converterId,
        }))
      }
    }
    setState((current: ConversionState) => {
      let next = reconcileInputs(current, buildConverterInputs(restoredText, restoredAttachments))
      if (pipelines) {
        for (const pieceType of Object.keys(next.pipelines)) {
          if (!restoredPipelines[pieceType]) next = changePipeline(next, pieceType, [])
        }
      }
      for (const [pieceType, stages] of Object.entries(restoredPipelines)) {
        next = changePipeline(next, pieceType, stages)
      }
      return {
        ...next, applied: { ...conversions }, workingInputs: {}, stageResults: {}, errors: {}, runId: 0, isConverting: false,
        runOwners: {}, activeRuns: [],
      }
    })
  }, [])

  const snapshot = (): ChatConverterSnapshot => ({
    generation: state.generation,
    workingInputs: state.workingInputs,
    pipelines: state.pipelines,
    stageResults: state.stageResults,
    errors: state.errors,
    applied: state.applied,
  })
  const restoreSnapshot = (
    saved: ChatConverterSnapshot, text: string, attachments: MessageAttachment[],
  ): void => {
    if (saved.generation !== generation) {
      throw new Error('The converter runtime changed. The previous converter state cannot be restored.')
    }
    const runId = ++nextRunId.current
    setState((current: ConversionState) => ({
      ...reconcileInputs(current, buildConverterInputs(text, attachments)),
      ...saved,
      pipelineRevisions: Object.fromEntries(
        Object.keys({ ...current.pipelines, ...saved.pipelines }).map((pieceType: string) => (
          [pieceType, (current.pipelineRevisions[pieceType] ?? 0) + 1]
        )),
      ),
      runId,
      isConverting: false,
      runOwners: {},
      activeRuns: [],
    }))
  }

  const hasEditedConversion = state.inputs.some((input: VersionedInput) => (
    state.workingInputs[input.id] !== undefined && state.workingInputs[input.id] !== input.value
  )) || Object.values(state.stageResults).some((stages: ConverterStageResult[]) => stages.some(
    (stage: ConverterStageResult) => stage.value !== stage.generated.output_value,
  )) || Object.values(state.applied).some((conversion: PieceConversion) => conversion.manuallyEdited)
  const hasPartialConversion = state.inputs.some((input: VersionedInput) => input.value.includes('\u27ea'))
    || Object.values(state.stageResults).some((stages: ConverterStageResult[]) => stages.some(
      (stage: ConverterStageResult) => stage.generated.input_value.includes('\u27ea'),
    ))
    || (Object.keys(state.applied).length > 0 && state.inputs.some(
      (input: VersionedInput) => !state.applied[input.id] && Boolean(input.value || input.file),
    ))
    || state.inputs.some((input: VersionedInput) => {
      const stages = state.stageResults[input.id] ?? []
      return stages.length > 0 && stages.length !== (state.pipelines[input.pieceType]?.length ?? 0)
    })
  return {
    repeatDisabledReason: hasEditedConversion || hasPartialConversion
      ? 'Repeated sends are unavailable after editing a conversion or converting only part of a prompt. Send once or reset conversions.'
      : undefined,
    editRevision: state.nextRevision,
    inputs: state.inputs.filter((input: VersionedInput) => !options.selectedIds || options.selectedIds.has(input.id)),
    workingInputs: state.workingInputs,
    pipelines: state.pipelines,
    stageResults: state.stageResults,
    results: Object.fromEntries(Object.entries(completedResults(state)).filter(
      ([id]: [string, ConverterPreviewResponse]) => !options.selectedIds || options.selectedIds.has(id),
    )),
    errors: state.errors,
    applied: state.applied,
    isConverting: state.isConverting,
    addConverter,
    setPipeline,
    retainConverters,
    discardTemporarySettings,
    convert: (pieceType: string) => runConversion({ pieceType, includeIncomplete: true }),
    convertRemaining: (pieceId: string, stageId: string) => runConversion({ pieceId, afterStageId: stageId }),
    editInput,
    editStageOutput,
    apply,
    clear,
    clearAll,
    editConvertedValue,
    restore,
    snapshot,
    restoreSnapshot,
  }
}
