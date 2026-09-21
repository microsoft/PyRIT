import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { DragEvent, KeyboardEvent, ReactNode } from 'react'

import {
  Button,
  MessageBar,
  MessageBarBody,
  Spinner,
  Tab,
  TabList,
  Text,
} from '@fluentui/react-components'
import {
  DismissRegular,
  OpenRegular,
  PlayRegular,
  ReOrderDotsVerticalRegular,
} from '@fluentui/react-icons'

import CreateConverterDialog from '@/components/Registry/CreateConverterDialog'
import { convertersApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type {
  ChatConverterController, ConverterInputPiece, ConverterInstance, ConverterPipelineStage, ConverterStageResult,
} from '@/types'

import {
  PIECE_TYPE_TO_DATA_TYPE,
  basenameFromValue,
  buildMediaUrl,
  dataTypeToAttachmentKind,
  isPathDataType,
} from '../converterTypes'
import { useConverterPanelStyles } from './ConverterPanel.styles'
import ConversionTextEditor from './ConversionTextEditor'
import SelectConverterInput from './SelectConverterInput'

const PIECE_TYPE_LABELS: Record<string, string> = {
  text: 'Text',
  image: 'Image',
  audio: 'Audio',
  video: 'Video',
  file: 'File',
}

const OUTPUT_TYPE_ORDER = ['text', 'image_path', 'audio_path', 'video_path', 'binary_path']

const MIN_PANEL_WIDTH = 480
const MAX_PANEL_WIDTH = 1200
const DEFAULT_PANEL_WIDTH = 800

interface ValuePreviewProps {
  dataType: string
  emptyText: string
  label?: string
  sectionTestId?: string
  testId?: string
  value?: string
  editorLabel?: string
  allowSelection?: boolean
  edited?: boolean
  onChange?: (value: string) => void
}

interface ConverterPanelProps {
  onClose: () => void
  controller: ChatConverterController
}

interface SelectedConverter extends ConverterInstance {
  readonly stageId: string
}

function formatDataType(dataType: string): string {
  return dataType
    .replace('_path', '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (character: string) => character.toUpperCase())
}

function ValuePreview({
  dataType,
  emptyText,
  label,
  sectionTestId,
  testId,
  value = '',
  editorLabel,
  allowSelection = false,
  edited = false,
  onChange,
}: ValuePreviewProps) {
  const styles = useConverterPanelStyles()
  const accessibleLabel = label ?? 'Converted output'

  let content: ReactNode
  if (dataType === 'text' && onChange) {
    content = <ConversionTextEditor
      value={value}
      label={editorLabel ?? accessibleLabel}
      placeholder={emptyText}
      allowSelection={allowSelection}
      onChange={onChange}
    />
  } else if (!value) {
    content = <Text className={styles.emptyPreview}>{emptyText}</Text>
  } else if (!isPathDataType(dataType)) {
    content = <pre className={styles.previewPre}>{value}</pre>
  } else {
    const mediaUrl = value.startsWith('blob:') ? value : buildMediaUrl(value)
    const attachmentKind = dataTypeToAttachmentKind(dataType)
    if (attachmentKind === 'image') {
      content = <img className={styles.previewImage} src={mediaUrl} alt={`${accessibleLabel} preview`} />
    } else if (attachmentKind === 'audio') {
      content = (
        <audio className={styles.previewAudio} src={mediaUrl} controls aria-label={`${accessibleLabel} preview`} />
      )
    } else if (attachmentKind === 'video') {
      content = (
        <video className={styles.previewVideo} src={mediaUrl} controls aria-label={`${accessibleLabel} preview`} />
      )
    } else {
      content = (
        <div className={styles.fileChip}>
          <Text className={styles.fileChipName}>
            {basenameFromValue(value, 'converted-file')}
          </Text>
          <a
            className={styles.fileChipOpen}
            href={mediaUrl}
            target="_blank"
            rel="noreferrer"
          >
            <OpenRegular />
            Open
          </a>
        </div>
      )
    }
  }

  return (
    <section className={styles.valueSection} data-testid={sectionTestId}>
      {label && (
        <Text className={styles.valueLabel} size={200} weight="semibold">
          {label}
        </Text>
      )}
      {edited && <Text size={200} className={styles.hintText}>Edited</Text>}
      <div className={styles.outputBox} data-testid={testId}>
        {content}
      </div>
    </section>
  )
}

export default function ConverterPanel({
  onClose,
  controller,
}: ConverterPanelProps) {
  const styles = useConverterPanelStyles()
  const [converters, setConverters] = useState<ConverterInstance[]>([])
  const [activeTab, setActiveTab] = useState('text')
  const {
    inputs, workingInputs, pipelines, stageResults, results, errors, isConverting,
    addConverter, setPipeline, retainConverters,
  } = controller
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [createDialogOpen, setCreateDialogOpen] = useState(false)
  const [panelWidth, setPanelWidth] = useState(DEFAULT_PANEL_WIDTH)
  const isResizing = useRef(false)
  const draggedConverterIndex = useRef<number | null>(null)

  const tabs = useMemo(() => {
    const seen = new Set(['text'])
    const result = ['text']
    for (const input of inputs) {
      const inputType = input.pieceType
      if (!seen.has(inputType)) {
        result.push(inputType)
        seen.add(inputType)
      }
    }
    return result
  }, [inputs])

  // The selected tab can disappear when an attachment is removed; fall back to
  // text for every derivation instead of resetting state from an effect.
  const effectiveActiveTab = tabs.includes(activeTab) ? activeTab : 'text'
  const selectedStages = useMemo(
    () => pipelines[effectiveActiveTab] ?? [],
    [effectiveActiveTab, pipelines],
  )
  const activeInputs = inputs.filter((input: ConverterInputPiece) => input.pieceType === effectiveActiveTab)
  const activeDataType = activeInputs[0]?.dataType ?? PIECE_TYPE_TO_DATA_TYPE[effectiveActiveTab]

  const loadConverters = useCallback(async (
    selectId?: string,
    selectPieceType?: string,
  ): Promise<void> => {
    setIsLoading(true)
    try {
      const response = await convertersApi.listConverters()
      setConverters(response.items)
      setError(null)
      const availableIds = new Set(
        response.items.map((converter: ConverterInstance) => converter.converter_id),
      )
      retainConverters(availableIds)
      if (selectId && selectPieceType && availableIds.has(selectId)) {
        addConverter(selectPieceType, selectId)
      }
    } catch (loadError) {
      setConverters([])
      setError(toApiError(loadError).detail)
    } finally {
      setIsLoading(false)
    }
  }, [retainConverters, addConverter])

  useEffect(() => {
    let cancelled = false
    void convertersApi.listConverters()
      .then((response) => {
        if (cancelled) return
        setConverters(response.items)
        retainConverters(new Set(response.items.map((converter: ConverterInstance) => converter.converter_id)))
        setError(null)
      })
      .catch((loadError: unknown) => {
        if (cancelled) return
        setConverters([])
        setError(toApiError(loadError).detail)
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [retainConverters])

  const selectedConverters = useMemo(
    () => selectedStages.flatMap((stage: ConverterPipelineStage): SelectedConverter[] => {
      const converter = converters.find((candidate: ConverterInstance) => candidate.converter_id === stage.converterId)
      return converter ? [{ ...converter, stageId: stage.id }] : []
    }),
    [converters, selectedStages],
  )

  // Offer converters that accept whatever the pipeline currently emits, so a
  // chain can legally continue past a converter that changes the data type.
  const lastSelectedConverter = selectedConverters[selectedConverters.length - 1]
  const nextInputType = lastSelectedConverter?.identifier.supported_output_types?.[0] ?? activeDataType

  const filteredConverters = converters.filter((converter: ConverterInstance) => {
    const supported = converter.identifier.supported_input_types ?? []
    return supported.length === 0 || supported.includes(nextInputType)
  })

  const groups: Record<string, ConverterInstance[]> = {}
  for (const converter of filteredConverters) {
    const outputType = converter.identifier.supported_output_types?.[0] ?? 'text'
    if (!groups[outputType]) groups[outputType] = []
    groups[outputType].push(converter)
  }
  const unknownTypes = Object.keys(groups).filter(
    (type: string) => !OUTPUT_TYPE_ORDER.includes(type),
  )
  const groupedConverters = [...OUTPUT_TYPE_ORDER, ...unknownTypes]
    .filter((type: string) => groups[type]?.length)
    .map((type: string) => ({ type, converters: groups[type] }))

  const activePipelineConfigured = selectedStages.length > 0
  const convertibleInputs = activeInputs.filter(
    (input: ConverterInputPiece) => (workingInputs[input.id] ?? input.value).trim(),
  )

  const handleConverterSelect = useCallback((converterId: string): void => {
    addConverter(effectiveActiveTab, converterId)
  }, [addConverter, effectiveActiveTab])

  const removeConverter = useCallback((index: number): void => {
    setPipeline(effectiveActiveTab, (stages: ConverterPipelineStage[]) => stages.filter(
      (_: ConverterPipelineStage, currentIndex: number) => currentIndex !== index,
    ))
  }, [setPipeline, effectiveActiveTab])

  const moveConverter = useCallback((sourceIndex: number, targetIndex: number): void => {
    if (
      sourceIndex === targetIndex
      || sourceIndex < 0
      || targetIndex < 0
      || sourceIndex >= selectedStages.length
      || targetIndex >= selectedStages.length
    ) {
      return
    }
    setPipeline(effectiveActiveTab, (stages: ConverterPipelineStage[]) => {
      const nextPipeline = [...stages]
      const [movedConverter] = nextPipeline.splice(sourceIndex, 1)
      nextPipeline.splice(targetIndex, 0, movedConverter)
      return nextPipeline
    })
  }, [setPipeline, effectiveActiveTab, selectedStages.length])

  const handleDragStart = useCallback((
    event: DragEvent<HTMLElement>,
    index: number,
  ): void => {
    draggedConverterIndex.current = index
    event.dataTransfer.effectAllowed = 'move'
    event.dataTransfer.setData('text/plain', String(index))
  }, [])

  const handleDrop = useCallback((
    event: DragEvent<HTMLElement>,
    targetIndex: number,
  ): void => {
    const sourceIndex = draggedConverterIndex.current
    draggedConverterIndex.current = null
    if (sourceIndex === null) return
    event.preventDefault()
    moveConverter(sourceIndex, targetIndex)
  }, [moveConverter])

  const handleReorderKeyDown = useCallback((
    event: KeyboardEvent<HTMLButtonElement>,
    index: number,
  ): void => {
    if (event.key === 'ArrowUp') {
      event.preventDefault()
      moveConverter(index, index - 1)
    } else if (event.key === 'ArrowDown') {
      event.preventDefault()
      moveConverter(index, index + 1)
    }
  }, [moveConverter])

  const handleMouseDown = useCallback((): void => {
    isResizing.current = true
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }, [])

  useEffect(() => {
    const handleMouseMove = (event: MouseEvent): void => {
      if (!isResizing.current) return
      setPanelWidth(Math.max(MIN_PANEL_WIDTH, Math.min(MAX_PANEL_WIDTH, event.clientX)))
    }
    const handleMouseUp = (): void => {
      if (!isResizing.current) return
      isResizing.current = false
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
    document.addEventListener('mousemove', handleMouseMove)
    document.addEventListener('mouseup', handleMouseUp)
    return () => {
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleMouseUp)
    }
  }, [])

  return (
    <div className={styles.resizeContainer} style={{ width: panelWidth }}>
      <aside className={styles.root} data-testid="converter-panel">
        <div className={styles.header}>
          <div className={styles.headerTitle}>
            <Text weight="semibold" size={300}>Converters</Text>
            <Text size={200} className={styles.hintText}>
              Build and convert registered converter pipelines.
            </Text>
          </div>
          <Button
            appearance="subtle"
            size="small"
            icon={<DismissRegular />}
            onClick={onClose}
            className={styles.touchTarget}
            aria-label="Close converters"
            data-testid="close-converter-panel-btn"
          />
        </div>
        {tabs.length > 1 && (
          <TabList
            selectedValue={effectiveActiveTab}
            onTabSelect={(_: unknown, data: { value: unknown }) => setActiveTab(String(data.value))}
            size="small"
            className={styles.tabBar}
            data-testid="converter-piece-tabs"
          >
            {tabs.map((tab: string) => {
              const converterCount = pipelines[tab]?.length ?? 0
              const label = PIECE_TYPE_LABELS[tab] ?? formatDataType(tab)
              return (
                <Tab key={tab} value={tab} data-testid={`converter-tab-${tab}`}>
                  {converterCount > 0 ? `${label} (${converterCount})` : label}
                </Tab>
              )
            })}
          </TabList>
        )}
        <div className={styles.body}>
          {!isLoading && !error && (
            <SelectConverterInput
              groupedConverters={groupedConverters}
              onOptionSelect={handleConverterSelect}
              onCreateNew={() => setCreateDialogOpen(true)}
            />
          )}
          {activeInputs.map((input: ConverterInputPiece) => <ValuePreview
            key={input.id}
            dataType={input.dataType}
            emptyText={
              effectiveActiveTab === 'text'
                ? 'Enter a prompt in the chat input.'
                : `Attach a ${effectiveActiveTab} file in the chat input.`
            }
            label={input.pieceType === 'text' ? 'Input - Text' : `Input - ${input.name}`}
            editorLabel={`Working input - ${input.name}`}
            allowSelection={selectedConverters.length > 0}
            edited={workingInputs[input.id] !== undefined && workingInputs[input.id] !== input.value}
            onChange={input.dataType === 'text'
              ? (value: string) => controller.editInput(input.id, value)
              : undefined}
            testId="converter-input-value"
            value={workingInputs[input.id] ?? input.value}
          />)}
          {isLoading && (
            <div className={styles.loading} data-testid="converter-panel-loading">
              <Spinner size="tiny" />
            </div>
          )}
          {!isLoading && error && (
            <MessageBar intent="error" data-testid="converter-panel-error">
              <MessageBarBody>{error}</MessageBarBody>
            </MessageBar>
          )}
          {!isLoading && !error && (
            <div className={styles.converterList} data-testid="converter-panel-list">
              {activePipelineConfigured && (
                <Button
                  appearance="primary"
                  size="small"
                  icon={isConverting ? <Spinner size="tiny" /> : <PlayRegular />}
                  onClick={() => void controller.convert(effectiveActiveTab)}
                  disabled={isConverting || convertibleInputs.length === 0}
                  className={styles.previewButton}
                  title={`Convert the configured ${PIECE_TYPE_LABELS[effectiveActiveTab] ?? effectiveActiveTab} chain.`}
                  data-testid="converter-preview-btn"
                >
                  {isConverting ? 'Converting...' : 'Convert'}
                </Button>
              )}
              {inputs.filter((input: ConverterInputPiece) => errors[input.id]).map((input: ConverterInputPiece) => (
                <MessageBar key={input.id} intent="error" data-testid="converter-preview-error">
                  <MessageBarBody className={styles.errorBody}>
                    {input.name}: {errors[input.id]} This piece is not converted.
                  </MessageBarBody>
                </MessageBar>
              ))}
              {selectedConverters.map((converter: SelectedConverter, index: number) => {
                const matchingStages = selectedStages.filter(
                  (stage: ConverterPipelineStage) => stage.converterId === converter.converter_id,
                )
                const occurrenceNumber = selectedStages
                  .slice(0, index + 1)
                  .filter((stage: ConverterPipelineStage) => stage.converterId === converter.converter_id)
                  .length
                const duplicateStageContext = matchingStages.length > 1
                  ? `, stage ${occurrenceNumber} of ${matchingStages.length}`
                  : ''
                const cardTestIdSuffix = occurrenceNumber > 1 ? `-${occurrenceNumber}` : ''
                return (
                  <div
                    key={converter.stageId}
                    className={styles.converterCard}
                    data-testid={`converter-item-${converter.converter_id}${cardTestIdSuffix}`}
                    onDragOver={(event: DragEvent<HTMLDivElement>) => {
                      if (draggedConverterIndex.current !== null) event.preventDefault()
                    }}
                    onDrop={(event: DragEvent<HTMLDivElement>) => handleDrop(event, index)}
                  >
                    <div
                      className={styles.converterCardHeader}
                      draggable
                      data-testid={`converter-drag-area-${index}`}
                      onDragStart={(event: DragEvent<HTMLDivElement>) => {
                        if ((event.target as HTMLElement).closest('[data-no-drag]')) {
                          event.preventDefault()
                          return
                        }
                        handleDragStart(event, index)
                      }}
                      onDragEnd={() => { draggedConverterIndex.current = null }}
                    >
                      <Button
                        appearance="subtle"
                        size="small"
                        icon={<ReOrderDotsVerticalRegular />}
                        className={styles.dragHandle}
                        aria-label={`Reorder converter ${converter.converter_id}${duplicateStageContext}`}
                        title="Drag this header to reorder. Use the arrow keys for keyboard reordering."
                        onKeyDown={(event: KeyboardEvent<HTMLButtonElement>) => handleReorderKeyDown(event, index)}
                      />
                      <Text weight="semibold" size={300} className={styles.converterName}>
                        {converter.converter_id}
                      </Text>
                      {converter.is_llm_based && <span className={styles.llmBadge}>LLM</span>}
                      <Button
                        appearance="subtle"
                        size="small"
                        icon={<DismissRegular />}
                        data-no-drag
                        aria-label={`Remove converter ${converter.converter_id}${duplicateStageContext}`}
                        onClick={() => removeConverter(index)}
                        className={styles.touchTarget}
                      />
                    </div>
                    {converter.identifier.class_name !== converter.converter_id && (
                      <Text size={200} className={styles.hintText}>
                        {converter.identifier.class_name}
                      </Text>
                    )}
                    <Text size={200} className={styles.hintText}>
                      {converter.description || 'No description is available.'}
                    </Text>
                    {activeInputs.map((input: ConverterInputPiece) => {
                      const stage = stageResults[input.id]?.find(
                        (result: ConverterStageResult) => result.stageId === converter.stageId,
                      )
                      const hasRemaining = index < selectedConverters.length - 1
                      const outputType = stage?.generated.output_data_type
                        ?? converter.identifier.supported_output_types?.[0] ?? 'text'
                      return (
                        <div key={input.id} className={styles.valueSection}>
                          <ValuePreview
                            label={activeInputs.length > 1 ? input.name : undefined}
                            editorLabel={`Stage ${index + 1} output - ${input.name}`}
                            dataType={outputType}
                            emptyText={stage
                              ? 'This stage returned an empty value.'
                              : 'Choose Convert above to see this stage output.'}
                            sectionTestId={`converter-stage-output-${index}`}
                            testId={results[input.id] && !hasRemaining ? 'converter-preview-result' : undefined}
                            value={stage?.value}
                            edited={stage !== undefined && stage.value !== stage.generated.output_value}
                            allowSelection={hasRemaining}
                            onChange={stage && outputType === 'text'
                              ? (value: string) => controller.editStageOutput(input.id, converter.stageId, value)
                              : undefined}
                          />
                          {hasRemaining && (
                            <Button
                              size="small"
                              icon={<PlayRegular />}
                              className={styles.previewButton}
                              disabled={isConverting || !stage?.value.trim()}
                              aria-label={`Convert ${input.name} from stage ${index + 2} to end`}
                              title="Convert all remaining stages from this value."
                              onClick={() => void controller.convertRemaining(input.id, converter.stageId)}
                            >
                              Convert
                            </Button>
                          )}
                        </div>
                      )
                    })}
                  </div>
                )
              })}
              <Button
                appearance="primary"
                onClick={controller.apply}
                disabled={isConverting || Object.keys(results).length === 0}
                className={styles.addConvertedButton}
                data-testid="use-converted-btn"
              >
                Add converted value
              </Button>
            </div>
          )}
        </div>
      </aside>
      <div
        className={styles.resizeHandle}
        onMouseDown={handleMouseDown}
        data-testid="converter-panel-resize"
      />
      <CreateConverterDialog
        open={createDialogOpen}
        onClose={() => setCreateDialogOpen(false)}
        onCreated={(converterId: string) => {
          setCreateDialogOpen(false)
          void loadConverters(converterId, effectiveActiveTab)
        }}
      />
    </div>
  )
}
