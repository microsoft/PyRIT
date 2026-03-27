import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Button, Combobox, Field, Input, MessageBar, MessageBarBody, Option, Select, Spinner, Switch, Tab, TabList, Text, Tooltip } from '@fluentui/react-components'
import { ChevronDownRegular, ChevronRightRegular, DismissRegular, InfoRegular, PlayRegular } from '@fluentui/react-icons'
import { convertersApi } from '../../services/api'
import { toApiError } from '../../services/errors'
import type { ConverterCatalogEntry } from '../../types'
import { useConverterPanelStyles } from './ConverterPanel.styles'

const PIECE_TYPE_LABELS: Record<string, string> = {
  text: 'Text',
  image: 'Image',
  audio: 'Audio',
  video: 'Video',
}

import type { PieceConversion } from './converterTypes'
import { PIECE_TYPE_TO_DATA_TYPE } from './converterTypes'

interface ConverterPanelProps {
  onClose: () => void
  previewText?: string
  attachmentData?: Record<string, string>
  activeInputTypes?: string[]
  onUseConvertedValue?: (conversion: PieceConversion) => void
}

export default function ConverterPanel({ onClose, previewText = '', attachmentData = {}, activeInputTypes = ['text'], onUseConvertedValue }: ConverterPanelProps) {
  const styles = useConverterPanelStyles()
  const [converters, setConverters] = useState<ConverterCatalogEntry[]>([])
  const [activeTab, setActiveTab] = useState('text')
  const [selectedConverterType, setSelectedConverterType] = useState('')
  const [query, setQuery] = useState('')
  const [paramValues, setParamValues] = useState<Record<string, string>>({})
  const [paramsExpanded, setParamsExpanded] = useState(true)
  const [previewOutput, setPreviewOutput] = useState('')
  const [previewConverterInstanceId, setPreviewConverterInstanceId] = useState<string | null>(null)
  const [isPreviewing, setIsPreviewing] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [showValidation, setShowValidation] = useState(false)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let isMounted = true

    const loadConverters = async () => {
      setIsLoading(true)
      setError(null)

      try {
        const response = await convertersApi.listConverterCatalog()
        if (!isMounted) {
          return
        }
        setConverters(response.items)
      } catch (err) {
        if (!isMounted) {
          return
        }
        setConverters([])
        setSelectedConverterType('')
        setQuery('')
        setError(toApiError(err).detail)
      } finally {
        if (isMounted) {
          setIsLoading(false)
        }
      }
    }

    void loadConverters()

    return () => {
      isMounted = false
    }
  }, [])

  // Tabs: always show Text, plus one for each attachment type
  const tabs = useMemo(() => {
    const seen = new Set<string>()
    const result: string[] = ['text']
    seen.add('text')
    for (const t of activeInputTypes) {
      if (!seen.has(t) && t !== 'text') {
        result.push(t)
        seen.add(t)
      }
    }
    return result
  }, [activeInputTypes])

  // Reset to text tab when tabs change and active tab is no longer available
  useEffect(() => {
    if (!tabs.includes(activeTab)) {
      setActiveTab('text')
    }
  }, [tabs, activeTab])

  // Filter converters by the active tab's input type
  const activeDataType = PIECE_TYPE_TO_DATA_TYPE[activeTab] ?? 'text'

  const filteredConverters = useMemo(() => {
    let filtered = converters.filter((c) => {
      const supported = c.supported_input_types ?? []
      if (supported.length === 0) return true
      return supported.includes(activeDataType)
    })
    if (query !== selectedConverterType) {
      filtered = filtered.filter((c) => c.converter_type.toLowerCase().includes(query.toLowerCase()))
    }
    return filtered
  }, [converters, query, selectedConverterType, activeDataType])

  // Group filtered converters by their primary output type
  const groupedConverters = useMemo(() => {
    const groups: Record<string, typeof filteredConverters> = {}
    const order = ['text', 'image_path', 'audio_path', 'video_path', 'binary_path']
    for (const c of filteredConverters) {
      const outType = (c.supported_output_types ?? [])[0] ?? 'text'
      if (!groups[outType]) groups[outType] = []
      groups[outType].push(c)
    }
    return order.filter((t) => groups[t]?.length).map((t) => ({ type: t, converters: groups[t] }))
  }, [filteredConverters])

  const selectedConverter = converters.find(
    (converter) => converter.converter_type === selectedConverterType
  )

  const missingRequiredParams = useMemo(() => {
    if (!selectedConverter) return []
    return (selectedConverter.parameters ?? [])
      .filter((p) => p.required && !paramValues[p.name]?.trim())
      .map((p) => p.name)
  }, [selectedConverter, paramValues])

  const hasRequiredParamErrors = missingRequiredParams.length > 0

  // Cache converter instance ID to avoid creating duplicate instances in the
  // backend registry on every preview. Only create a new instance when the
  // converter type or parameters change.
  const cachedInstanceRef = useRef<{ type: string; params: string; id: string } | null>(null)

  const getOrCreateConverterInstance = async (type: string, params: Record<string, string>): Promise<string> => {
    const paramsKey = JSON.stringify(params)
    if (cachedInstanceRef.current?.type === type && cachedInstanceRef.current?.params === paramsKey) {
      return cachedInstanceRef.current.id
    }
    const response = await convertersApi.createConverter({ type, params: { ...params } })
    cachedInstanceRef.current = { type, params: paramsKey, id: response.converter_id }
    return response.converter_id
  }

  const handlePreview = async () => {
    // For text tab, use chat input text; for other tabs, use attachment data
    const previewValue = activeTab === 'text' ? previewText : (attachmentData[activeTab] ?? '')
    if (!selectedConverterType || !previewValue.trim()) {
      return
    }
    if (hasRequiredParamErrors) {
      setShowValidation(true)
      return
    }
    setShowValidation(false)
    setIsPreviewing(true)
    setPreviewError(null)
    setPreviewOutput('')

    try {
      const converterId = await getOrCreateConverterInstance(selectedConverterType, paramValues)

      const previewResponse = await convertersApi.previewConversion({
        original_value: previewValue,
        converter_ids: [converterId],
        original_value_data_type: activeDataType,
      })

      setPreviewOutput(previewResponse.converted_value)
      setPreviewConverterInstanceId(converterId)
    } catch (err) {
      setPreviewError(toApiError(err).detail)
    } finally {
      setIsPreviewing(false)
    }
  }

  // Auto-preview for non-LLM text-output converters (they're fast/cheap)
  const autoPreviewTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => {
    if (autoPreviewTimer.current) {
      clearTimeout(autoPreviewTimer.current)
      autoPreviewTimer.current = null
    }

    const currentPreviewValue = activeTab === 'text' ? previewText : (attachmentData[activeTab] ?? '')

    // Clear preview when input is emptied (e.g. after sending)
    if (!currentPreviewValue.trim()) {
      setPreviewOutput('')
      setPreviewConverterInstanceId(null)
      setPreviewError(null)
    }

    if (
      !selectedConverter ||
      selectedConverter.is_llm_based ||
      !currentPreviewValue.trim() ||
      hasRequiredParamErrors
    ) {
      return
    }

    autoPreviewTimer.current = setTimeout(() => {
      handlePreview()
    }, 300)

    return () => {
      if (autoPreviewTimer.current) {
        clearTimeout(autoPreviewTimer.current)
      }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedConverterType, previewText, paramValues, selectedConverter])

  const [panelWidth, setPanelWidth] = useState(320)
  const isDragging = useRef(false)

  const handleMouseDown = useCallback(() => {
    isDragging.current = true
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }, [])

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isDragging.current) return
      const newWidth = Math.max(240, Math.min(600, e.clientX))
      setPanelWidth(newWidth)
    }
    const handleMouseUp = () => {
      if (!isDragging.current) return
      isDragging.current = false
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
    <div className={styles.resizeContainer} style={{ width: panelWidth, minWidth: panelWidth }}>
      <aside className={styles.root} data-testid="converter-panel">
        <div className={styles.header}>
        <div className={styles.headerTitle}>
          <Text weight="semibold" size={300}>Converters</Text>
          <Text size={200} className={styles.hintText}>
            Select and preview prompt converters here in the next step.
          </Text>
        </div>
        <Button
          appearance="subtle"
          size="small"
          icon={<DismissRegular />}
          onClick={onClose}
          data-testid="close-converter-panel-btn"
        />
      </div>
      {tabs.length > 1 && (
        <TabList
          selectedValue={activeTab}
          onTabSelect={(_, data) => {
            const newTab = data.value as string
            setActiveTab(newTab)
            setSelectedConverterType('')
            setQuery('')
            setParamValues({})
            setPreviewOutput('')
            setPreviewConverterInstanceId(null)
            setPreviewError(null)
            setShowValidation(false)
            cachedInstanceRef.current = null
          }}
          size="small"
          className={styles.tabBar}
          data-testid="converter-piece-tabs"
        >
          {tabs.map((t) => (
            <Tab key={t} value={t} data-testid={`converter-tab-${t}`}>
              {PIECE_TYPE_LABELS[t] ?? t}
            </Tab>
          ))}
        </TabList>
      )}
      <div className={styles.body}>
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

        {!isLoading && !error && converters.length === 0 && (
          <div className={styles.emptyState} data-testid="converter-panel-empty">
            <Text size={300}>No converter types are currently available.</Text>
            <Text size={200} className={styles.hintText}>
              Once the backend converter catalog is available, converter types will appear here.
            </Text>
          </div>
        )}

        {!isLoading && !error && converters.length > 0 && (
          <div className={styles.converterList} data-testid="converter-panel-list">
            <Field label="Converter">
              <Combobox
                value={query}
                selectedOptions={selectedConverterType ? [selectedConverterType] : []}
                onOptionSelect={(_, data) => {
                  const newType = data.optionValue ?? ''
                  setSelectedConverterType(newType)
                  setQuery(data.optionText ?? '')
                  // Reset param values to defaults for the newly selected converter
                  const newConverter = converters.find((c) => c.converter_type === newType)
                  const defaults: Record<string, string> = {}
                  for (const p of newConverter?.parameters ?? []) {
                    if (p.default_value != null) {
                      defaults[p.name] = p.default_value
                    }
                  }
                  setParamValues(defaults)
                  setPreviewOutput('')
                  setPreviewConverterInstanceId(null)
                  setPreviewError(null)
                  setShowValidation(false)
                }}
                onChange={(e) => setQuery((e.target as HTMLInputElement).value)}
                placeholder="Search converters..."
                data-testid="converter-panel-select"
              >
                {groupedConverters.map((group) => (
                  <React.Fragment key={group.type}>
                    <Option key={`__header_${group.type}`} text="" disabled value="">
                      <span className={`${styles.groupHeader} ${styles[`header_${group.type}` as keyof typeof styles] ?? ''}`}>
                        — {group.type.replace('_path', '')} output —
                      </span>
                    </Option>
                    {group.converters.map((converter) => (
                      <Option key={converter.converter_type} value={converter.converter_type} text={converter.converter_type} data-testid={`converter-option-${converter.converter_type}`}>
                        <span className={styles.optionContent}>
                          {converter.converter_type}
                          {converter.is_llm_based && <span className={styles.llmBadge}>LLM</span>}
                        </span>
                      </Option>
                    ))}
                  </React.Fragment>
                ))}
              </Combobox>
            </Field>
            {selectedConverter && (
              <div
                className={styles.converterCard}
                data-testid={`converter-item-${selectedConverter.converter_type}`}
              >
                <Text weight="semibold" size={300} className={styles.converterName}>
                  {selectedConverter.converter_type}
                </Text>
                {selectedConverter.description && (
                  <Text size={200} className={styles.hintText}>
                    {selectedConverter.description}
                  </Text>
                )}
                <div className={styles.metaRow}>
                  <Text size={200} className={styles.badgeText}>In:</Text>
                  {(selectedConverter.supported_input_types ?? []).map((t) => (
                    <span key={t} className={`${styles.typeBadge} ${styles[`input_${t}` as keyof typeof styles] ?? ''}`}>
                      {t.replace('_path', '')}
                    </span>
                  ))}
                </div>
                <div className={styles.metaRow}>
                  <Text size={200} className={styles.badgeText}>Out:</Text>
                  {(selectedConverter.supported_output_types ?? []).map((t) => (
                    <span key={t} className={`${styles.typeBadge} ${styles[`output_${t}` as keyof typeof styles] ?? ''}`}>
                      {t.replace('_path', '')}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {selectedConverter && (selectedConverter.parameters?.length ?? 0) > 0 && (
              <div className={styles.paramsSection} data-testid="converter-params">
                <Button
                  appearance="transparent"
                  size="small"
                  icon={paramsExpanded ? <ChevronDownRegular /> : <ChevronRightRegular />}
                  onClick={() => setParamsExpanded((prev) => !prev)}
                  className={styles.paramsSectionHeader}
                  data-testid="toggle-params-btn"
                >
                  Parameters
                </Button>
                {paramsExpanded && (selectedConverter.parameters ?? []).map((param) => {
                  const isMissing = showValidation && param.required && !paramValues[param.name]?.trim()
                  return (
                  <div key={param.name} className={styles.paramBlock}>
                    <span className={styles.paramLabel}>
                      <Text size={200} weight="semibold">{param.name}{param.required ? ' *' : ''}</Text>
                      {param.description && (
                        <Tooltip content={param.description} relationship="description">
                          <span className={styles.paramInfo}><InfoRegular fontSize={12} /></span>
                        </Tooltip>
                      )}
                    </span>
                    {param.type_name === 'bool' || param.type_name === 'Optional[bool]' ? (
                      <Switch
                        checked={(paramValues[param.name] ?? param.default_value ?? 'false').toLowerCase() === 'true'}
                        onChange={(_, data) =>
                          setParamValues((prev) => ({ ...prev, [param.name]: data.checked ? 'true' : 'false' }))
                        }
                        label={(paramValues[param.name] ?? param.default_value ?? 'false').toLowerCase() === 'true' ? 'True' : 'False'}
                        data-testid={`param-${param.name}`}
                      />
                    ) : param.choices ? (
                      <Select
                        value={paramValues[param.name] ?? param.default_value ?? ''}
                        onChange={(_, data) =>
                          setParamValues((prev) => ({ ...prev, [param.name]: data.value }))
                        }
                        data-testid={`param-${param.name}`}
                      >
                        {param.choices.map((choice) => (
                          <option key={choice} value={choice}>
                            {choice}
                          </option>
                        ))}
                      </Select>
                    ) : /path|file/i.test(param.name) ? (
                      <div className={styles.filePickerRow}>
                        <Input
                          value={paramValues[param.name] ?? ''}
                          placeholder={param.default_value ?? 'Select a file...'}
                          onChange={(_, data) =>
                            setParamValues((prev) => ({ ...prev, [param.name]: data.value }))
                          }
                          className={isMissing ? styles.paramInputError : undefined}
                          data-testid={`param-${param.name}`}
                        />
                        <Button
                          appearance="subtle"
                          size="small"
                          onClick={() => {
                            const input = document.createElement('input')
                            input.type = 'file'
                            input.onchange = () => {
                              const file = input.files?.[0]
                              if (file) {
                                // Use webkitRelativePath or name — for local backend the full path isn't available
                                // The user can also manually type/paste the path
                                setParamValues((prev) => ({ ...prev, [param.name]: file.name }))
                              }
                            }
                            input.click()
                          }}
                          data-testid={`param-${param.name}-browse`}
                        >
                          Browse
                        </Button>
                      </div>
                    ) : (
                      <Input
                        value={paramValues[param.name] ?? ''}
                        placeholder={param.default_value ?? undefined}
                        onChange={(_, data) =>
                          setParamValues((prev) => ({ ...prev, [param.name]: data.value }))
                        }
                        className={isMissing ? styles.paramInputError : undefined}
                        data-testid={`param-${param.name}`}
                      />
                    )}
                    {isMissing && (
                      <Text size={100} className={styles.paramErrorText}>Required</Text>
                    )}
                    {param.type_name !== 'bool' && param.type_name !== 'Optional[bool]' && !param.choices && (
                      <Text size={100} className={styles.hintText}>{param.type_name.replace(/^Optional\[(.+)\]$/, '$1')}</Text>
                    )}
                  </div>
                  )
                })}
              </div>
            )}

            <div className={styles.outputSection} data-testid="converter-preview-section">
              <Button
                appearance="primary"
                size="small"
                icon={isPreviewing ? <Spinner size="tiny" /> : <PlayRegular />}
                onClick={handlePreview}
                disabled={isPreviewing || !(activeTab === 'text' ? previewText.trim() : attachmentData[activeTab]) || !selectedConverterType}
                data-testid="converter-preview-btn"
              >
                {isPreviewing ? 'Converting...' : 'Preview'}
              </Button>

              {activeTab === 'text' && !previewText.trim() && (
                <Text size={200} className={styles.hintText}>
                  Type in the chat input box to preview a conversion.
                </Text>
              )}

              {activeTab !== 'text' && !attachmentData[activeTab] && (
                <Text size={200} className={styles.hintText}>
                  Attach a {activeTab} file to preview a conversion.
                </Text>
              )}

              {previewError && (
                <MessageBar intent="error" data-testid="converter-preview-error">
                  <MessageBarBody className={styles.errorBody}>{previewError}</MessageBarBody>
                </MessageBar>
              )}

              <div data-testid="converter-output">
                <Text weight="semibold" size={300}>Output</Text>
                <div className={styles.outputBox}>
                  {previewOutput ? (
                    previewOutput.match(/\.(png|jpg|jpeg|gif|bmp|webp)$/i) ? (
                      <img
                        src={`/api/media?path=${encodeURIComponent(previewOutput)}`}
                        alt="Converter output"
                        className={styles.previewImage}
                        data-testid="converter-preview-result"
                      />
                    ) : previewOutput.match(/\.(wav|mp3|ogg|flac)$/i) ? (
                      <audio
                        controls
                        src={`/api/media?path=${encodeURIComponent(previewOutput)}`}
                        className={styles.previewAudio}
                        data-testid="converter-preview-result"
                      />
                    ) : previewOutput.match(/\.(mp4|webm|mov)$/i) ? (
                      <video
                        controls
                        src={`/api/media?path=${encodeURIComponent(previewOutput)}`}
                        className={styles.previewVideo}
                        data-testid="converter-preview-result"
                      />
                    ) : (
                      <pre className={styles.previewPre} data-testid="converter-preview-result">{previewOutput}</pre>
                    )
                  ) : (
                    <Text size={200} className={styles.hintText}>
                      Converted output will appear here.
                    </Text>
                  )}
                </div>
              </div>

              {previewOutput && previewConverterInstanceId && (
                <Button
                  appearance="primary"
                  size="small"
                  onClick={() => onUseConvertedValue?.({
                    pieceType: activeTab,
                    converterInstanceId: previewConverterInstanceId,
                    convertedValue: previewOutput,
                    originalValue: activeTab === 'text' ? previewText : (attachmentData[activeTab] ?? ''),
                  })}
                  disabled={!onUseConvertedValue}
                  data-testid="use-converted-btn"
                >
                  Use Converted Value
                </Button>
              )}
            </div>
          </div>
        )}
      </div>
    </aside>
    <div
      className={styles.resizeHandle}
      onMouseDown={handleMouseDown}
      data-testid="converter-panel-resize"
    />
    </div>
  )
}
