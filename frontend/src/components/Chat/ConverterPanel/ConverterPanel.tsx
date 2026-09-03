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
import type { ConverterInstance, ConverterPreviewResponse } from '@/types'

import type { PieceConversion } from '../converterTypes'
import {
  PIECE_TYPE_TO_DATA_TYPE,
  basenameFromValue,
  buildMediaUrl,
  dataTypeToAttachmentKind,
  isPathDataType,
} from '../converterTypes'
import { useConverterPanelStyles } from './ConverterPanel.styles'
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

/** A converted result kept only while it still matches its input and pipeline. */
interface PreviewSnapshot {
  converterIds: string[]
  inputValue: string
  pieceType: string
  response: ConverterPreviewResponse
}

type PreviewOutcome =
  | { pieceType: string; snapshot: PreviewSnapshot }
  | { pieceType: string; error: string }

interface ValuePreviewProps {
  dataType: string
  emptyText: string
  label?: string
  sectionTestId?: string
  testId?: string
  value?: string
}

interface ConverterPanelProps {
  onClose: () => void
  previewText?: string
  attachmentData?: Record<string, string>
  activeInputTypes?: string[]
  onUseConvertedValues?: (conversions: PieceConversion[]) => void
}

function formatDataType(dataType: string): string {
  return dataType
    .replace('_path', '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (character: string) => character.toUpperCase())
}

function previewMatches(
  snapshot: PreviewSnapshot | undefined,
  inputValue: string,
  converterIds: string[],
): snapshot is PreviewSnapshot {
  return Boolean(
    snapshot
    && snapshot.inputValue === inputValue
    && snapshot.converterIds.length === converterIds.length
    && snapshot.converterIds.every(
      (converterId: string, index: number) => converterId === converterIds[index],
    ),
  )
}

/** Read-only rendering of a value, using a media player whenever the type allows. */
function ValuePreview({
  dataType,
  emptyText,
  label,
  sectionTestId,
  testId,
  value = '',
}: ValuePreviewProps) {
  const styles = useConverterPanelStyles()
  const accessibleLabel = label ?? 'Converted output'

  let content: ReactNode
  if (!value) {
    content = <Text className={styles.emptyPreview}>{emptyText}</Text>
  } else if (!isPathDataType(dataType)) {
    content = <pre className={styles.previewPre}>{value}</pre>
  } else {
    const mediaUrl = buildMediaUrl(value)
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
      <div className={styles.outputBox} data-testid={testId}>
        {content}
      </div>
    </section>
  )
}

export default function ConverterPanel({
  onClose,
  previewText = '',
  attachmentData = {},
  activeInputTypes = ['text'],
  onUseConvertedValues,
}: ConverterPanelProps) {
  const styles = useConverterPanelStyles()
  const [converters, setConverters] = useState<ConverterInstance[]>([])
  const [activeTab, setActiveTab] = useState('text')
  const [pipelines, setPipelines] = useState<Record<string, string[]>>({})
  const [previewResults, setPreviewResults] = useState<Record<string, PreviewSnapshot>>({})
  const [previewErrors, setPreviewErrors] = useState<Record<string, string>>({})
  const [isPreviewing, setIsPreviewing] = useState(false)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [createDialogOpen, setCreateDialogOpen] = useState(false)
  const [panelWidth, setPanelWidth] = useState(DEFAULT_PANEL_WIDTH)
  const isResizing = useRef(false)
  const draggedConverterIndex = useRef<number | null>(null)

  const tabs = useMemo(() => {
    const seen = new Set(['text'])
    const result = ['text']
    for (const inputType of activeInputTypes) {
      if (!seen.has(inputType)) {
        result.push(inputType)
        seen.add(inputType)
      }
    }
    return result
  }, [activeInputTypes])

  // The selected tab can disappear when an attachment is removed; fall back to
  // text for every derivation instead of resetting state from an effect.
  const effectiveActiveTab = tabs.includes(activeTab) ? activeTab : 'text'
  const selectedConverterIds = useMemo(
    () => pipelines[effectiveActiveTab] ?? [],
    [effectiveActiveTab, pipelines],
  )
  const activeDataType = PIECE_TYPE_TO_DATA_TYPE[effectiveActiveTab] ?? 'text'

  const inputValueFor = useCallback((pieceType: string): string => (
    pieceType === 'text' ? previewText : (attachmentData[pieceType] ?? '')
  ), [attachmentData, previewText])

  const clearPreviewFor = useCallback((pieceType: string): void => {
    setPreviewResults((current) => {
      if (!(pieceType in current)) return current
      const next = { ...current }
      delete next[pieceType]
      return next
    })
    setPreviewErrors((current) => {
      if (!(pieceType in current)) return current
      const next = { ...current }
      delete next[pieceType]
      return next
    })
  }, [])

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
      setPipelines((current) => {
        const next = Object.fromEntries(
          Object.entries(current).map(([pieceType, converterIds]) => [
            pieceType,
            converterIds.filter((converterId: string) => availableIds.has(converterId)),
          ]),
        )
        if (selectId && selectPieceType && availableIds.has(selectId)) {
          next[selectPieceType] = [...(next[selectPieceType] ?? []), selectId]
        }
        return next
      })
      if (selectPieceType) clearPreviewFor(selectPieceType)
    } catch (loadError) {
      setConverters([])
      setPipelines({})
      setPreviewResults({})
      setPreviewErrors({})
      setError(toApiError(loadError).detail)
    } finally {
      setIsLoading(false)
    }
  }, [clearPreviewFor])

  useEffect(() => {
    let cancelled = false
    void convertersApi.listConverters()
      .then((response) => {
        if (cancelled) return
        setConverters(response.items)
        setError(null)
      })
      .catch((loadError: unknown) => {
        if (cancelled) return
        setConverters([])
        setPipelines({})
        setPreviewResults({})
        setPreviewErrors({})
        setError(toApiError(loadError).detail)
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const selectedConverters = useMemo(
    () => selectedConverterIds
      .map((converterId: string) => converters.find(
        (converter: ConverterInstance) => converter.converter_id === converterId,
      ))
      .filter((converter: ConverterInstance | undefined): converter is ConverterInstance => (
        converter !== undefined
      )),
    [converters, selectedConverterIds],
  )

  // Offer converters that accept whatever the pipeline currently emits, so a
  // chain can legally continue past a converter that changes the data type.
  const lastSelectedConverter = selectedConverters[selectedConverters.length - 1]
  const nextInputType = lastSelectedConverter?.identifier.supported_output_types?.[0] ?? activeDataType

  const filteredConverters = useMemo(() => converters.filter((converter: ConverterInstance) => {
    const supported = converter.identifier.supported_input_types ?? []
    return supported.length === 0 || supported.includes(nextInputType)
  }), [converters, nextInputType])

  const groupedConverters = useMemo(() => {
    const groups: Record<string, ConverterInstance[]> = {}
    for (const converter of filteredConverters) {
      const outputType = converter.identifier.supported_output_types?.[0] ?? 'text'
      if (!groups[outputType]) groups[outputType] = []
      groups[outputType].push(converter)
    }
    const unknownTypes = Object.keys(groups).filter(
      (type: string) => !OUTPUT_TYPE_ORDER.includes(type),
    )
    return [...OUTPUT_TYPE_ORDER, ...unknownTypes]
      .filter((type: string) => groups[type]?.length)
      .map((type: string) => ({ type, converters: groups[type] }))
  }, [filteredConverters])

  const validPreviewResults = useMemo(() => Object.fromEntries(
    tabs.flatMap((pieceType: string) => {
      const converterIds = pipelines[pieceType] ?? []
      const snapshot = previewResults[pieceType]
      return previewMatches(snapshot, inputValueFor(pieceType), converterIds)
        ? [[pieceType, snapshot]]
        : []
    }),
  ) as Record<string, PreviewSnapshot>, [inputValueFor, pipelines, previewResults, tabs])

  const configuredPieceTypes = tabs.filter(
    (pieceType: string) => (pipelines[pieceType]?.length ?? 0) > 0,
  )
  const convertiblePieceTypes = configuredPieceTypes.filter(
    (pieceType: string) => inputValueFor(pieceType).trim().length > 0,
  )
  const currentInput = inputValueFor(effectiveActiveTab)
  const previewResponse = validPreviewResults[effectiveActiveTab]?.response

  const handleConverterSelect = useCallback((converterId: string): void => {
    setPipelines((current) => ({
      ...current,
      [effectiveActiveTab]: [...(current[effectiveActiveTab] ?? []), converterId],
    }))
    clearPreviewFor(effectiveActiveTab)
  }, [clearPreviewFor, effectiveActiveTab])

  const removeConverter = useCallback((index: number): void => {
    setPipelines((current) => ({
      ...current,
      [effectiveActiveTab]: (current[effectiveActiveTab] ?? []).filter(
        (_: string, currentIndex: number) => currentIndex !== index,
      ),
    }))
    clearPreviewFor(effectiveActiveTab)
  }, [clearPreviewFor, effectiveActiveTab])

  const moveConverter = useCallback((sourceIndex: number, targetIndex: number): void => {
    if (
      sourceIndex === targetIndex
      || sourceIndex < 0
      || targetIndex < 0
      || sourceIndex >= selectedConverterIds.length
      || targetIndex >= selectedConverterIds.length
    ) {
      return
    }
    setPipelines((current) => {
      const nextPipeline = [...(current[effectiveActiveTab] ?? [])]
      const [movedConverter] = nextPipeline.splice(sourceIndex, 1)
      nextPipeline.splice(targetIndex, 0, movedConverter)
      return { ...current, [effectiveActiveTab]: nextPipeline }
    })
    clearPreviewFor(effectiveActiveTab)
  }, [clearPreviewFor, effectiveActiveTab, selectedConverterIds.length])

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

  const handleConvert = useCallback(async (): Promise<void> => {
    if (convertiblePieceTypes.length === 0) return

    setIsPreviewing(true)
    const outcomes = await Promise.all(convertiblePieceTypes.map(
      async (pieceType: string): Promise<PreviewOutcome> => {
        const converterIds = [...(pipelines[pieceType] ?? [])]
        const inputValue = inputValueFor(pieceType)
        try {
          const response = await convertersApi.previewConversion({
            original_value: inputValue,
            converter_ids: converterIds,
            original_value_data_type: PIECE_TYPE_TO_DATA_TYPE[pieceType] ?? 'text',
          })
          return {
            pieceType,
            snapshot: { converterIds, inputValue, pieceType, response },
          }
        } catch (previewError) {
          return { pieceType, error: toApiError(previewError).detail }
        }
      },
    ))

    const nextResults: Record<string, PreviewSnapshot> = {}
    const nextErrors: Record<string, string> = {}
    for (const outcome of outcomes) {
      if ('snapshot' in outcome) {
        nextResults[outcome.pieceType] = outcome.snapshot
      } else {
        nextErrors[outcome.pieceType] = outcome.error
      }
    }
    setPreviewResults(nextResults)
    setPreviewErrors(nextErrors)
    setIsPreviewing(false)
  }, [convertiblePieceTypes, inputValueFor, pipelines])

  const successfulConversions = useMemo(() => tabs.flatMap((pieceType: string) => {
    const snapshot = validPreviewResults[pieceType]
    if (!snapshot) return []
    const conversion: PieceConversion = {
      pieceType,
      converterInstanceIds: snapshot.converterIds,
      convertedValue: snapshot.response.converted_value,
      originalValue: snapshot.inputValue,
      convertedDataType: snapshot.response.converted_value_data_type,
    }
    return [conversion]
  }), [tabs, validPreviewResults])

  const handleUseConvertedValues = useCallback((): void => {
    if (successfulConversions.length === 0 || !onUseConvertedValues) return
    onUseConvertedValues(successfulConversions)
  }, [onUseConvertedValues, successfulConversions])

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
          <ValuePreview
            dataType={activeDataType}
            emptyText={
              effectiveActiveTab === 'text'
                ? 'Enter a prompt in the chat input.'
                : `Attach a ${effectiveActiveTab} file in the chat input.`
            }
            label={`Input - ${formatDataType(activeDataType)}`}
            testId="converter-input-value"
            value={currentInput}
          />
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
              <SelectConverterInput
                groupedConverters={groupedConverters}
                onOptionSelect={handleConverterSelect}
                onCreateNew={() => setCreateDialogOpen(true)}
              />
              {configuredPieceTypes.length > 0 && (
                <Button
                  appearance="primary"
                  size="small"
                  icon={isPreviewing ? <Spinner size="tiny" /> : <PlayRegular />}
                  onClick={() => void handleConvert()}
                  disabled={isPreviewing || convertiblePieceTypes.length === 0}
                  className={styles.previewButton}
                  data-testid="converter-preview-btn"
                >
                  {isPreviewing ? 'Converting...' : 'Convert'}
                </Button>
              )}
              {previewErrors[effectiveActiveTab] && (
                <MessageBar intent="error" data-testid="converter-preview-error">
                  <MessageBarBody className={styles.errorBody}>
                    {previewErrors[effectiveActiveTab]}
                  </MessageBarBody>
                </MessageBar>
              )}
              {selectedConverters.map((converter: ConverterInstance, index: number) => {
                const matchingConverterIds = selectedConverterIds.filter(
                  (converterId: string) => converterId === converter.converter_id,
                )
                const occurrenceNumber = selectedConverterIds
                  .slice(0, index + 1)
                  .filter((converterId: string) => converterId === converter.converter_id)
                  .length
                const duplicateStageContext = matchingConverterIds.length > 1
                  ? `, stage ${occurrenceNumber} of ${matchingConverterIds.length}`
                  : ''
                const cardTestIdSuffix = occurrenceNumber > 1 ? `-${occurrenceNumber}` : ''
                const stage = previewResponse?.steps[index]
                const outputDataType = stage?.output_data_type
                  ?? converter.identifier.supported_output_types?.[0]
                  ?? 'text'
                return (
                  <div
                    key={`${converter.converter_id}-${index}`}
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
                    <ValuePreview
                      dataType={outputDataType}
                      emptyText={
                        previewResponse
                          ? 'This stage returned an empty value.'
                          : 'Choose Convert to see this stage output.'
                      }
                      sectionTestId={`converter-stage-output-${index}`}
                      testId={
                        previewResponse && index === selectedConverters.length - 1
                          ? 'converter-preview-result'
                          : undefined
                      }
                      value={stage?.output_value}
                    />
                  </div>
                )
              })}
              <Button
                appearance="primary"
                onClick={handleUseConvertedValues}
                disabled={successfulConversions.length === 0 || !onUseConvertedValues}
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
