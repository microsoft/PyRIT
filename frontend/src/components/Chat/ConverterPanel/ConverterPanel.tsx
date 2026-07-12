import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Button, MessageBar, MessageBarBody, Spinner, Tab, TabList, Text } from '@fluentui/react-components'
import { DismissRegular } from '@fluentui/react-icons'
import { convertersApi } from '../../../services/api'
import { toApiError } from '../../../services/errors'
import type { ConverterCatalogEntry } from '../../../types'
import type { PieceConversion } from '../converterTypes'
import { PIECE_TYPE_TO_DATA_TYPE } from '../converterTypes'
import { useConverterPanelStyles } from './ConverterPanel.styles'
import SelectConverterInput from './SelectConverterInput'
import ConverterPreview from './ConverterPreview'
import ConverterParams from './ConverterParams'

const PIECE_TYPE_LABELS: Record<string, string> = {
  text: 'Text',
  image: 'Image',
  audio: 'Audio',
  video: 'Video',
}

// Converter classes the backend can build but that aren't useful to offer in the
// picker (base/helper classes).
const HIDDEN_CONVERTER_TYPES = new Set<string>(['SelectiveTextConverter'])

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
  const [previewOutputType, setPreviewOutputType] = useState('text')
  const [previewConverterInstanceId, setPreviewConverterInstanceId] = useState<string | null>(null)
  const [isPreviewing, setIsPreviewing] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [showValidation, setShowValidation] = useState(false)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [panelWidth, setPanelWidth] = useState(320)
  const isDragging = useRef(false)

  useEffect(() => {
    let cancelled = false
    convertersApi.listConverterCatalog()
      .then((response) => {
        if (cancelled) return
        setConverters(response.items.filter((c) => !HIDDEN_CONVERTER_TYPES.has(c.converter_type)))
        setError(null)
      })
      .catch((err) => {
        if (cancelled) return
        setConverters([])
        setSelectedConverterType('')
        setQuery('')
        setError(toApiError(err).detail)
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  // Tabs: always show Text, plus one for each attachment type
  const tabs = useMemo(() => {
    const seen = new Set(['text'])
    const result: string[] = ['text']
    for (const t of activeInputTypes) {
      if (!seen.has(t)) {
        result.push(t)
        seen.add(t)
      }
    }
    return result
  }, [activeInputTypes])

  // If the selected tab disappears (e.g. user removes an attachment), fall back
  // to 'text' for any derivation that depends on the active tab. The actual
  // `activeTab` state is only updated on user click; deriving the effective
  // value avoids a setState-in-effect that react-hooks/set-state-in-effect would flag.
  const effectiveActiveTab = tabs.includes(activeTab) ? activeTab : 'text'

  // Filter converters by the active tab's input type
  const activeDataType = PIECE_TYPE_TO_DATA_TYPE[effectiveActiveTab] ?? 'text'

  const filteredConverters = useMemo(() => {
    let filtered = converters.filter((c) => {
      const supported = c.supported_input_types ?? []
      if (!supported.length) {
          return true
       }
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
      const outType = c.supported_output_types[0]
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

  // Cache converter instance ID to avoid creating duplicate instances in the
  // backend registry on every preview. Only create a new instance when the
  // converter type or parameters change.
  const cachedInstanceRef = useRef<{ type: string; params: string; id: string } | null>(null)

  const getOrCreateConverterInstance = useCallback(async (type: string, params: Record<string, string>): Promise<string> => {
    const paramsKey = JSON.stringify(params)
    if (cachedInstanceRef.current?.type === type && cachedInstanceRef.current?.params === paramsKey) {
      return cachedInstanceRef.current.id
    }
    const response = await convertersApi.createConverter({ type, params: { ...params } })
    cachedInstanceRef.current = { type, params: paramsKey, id: response.converter_id }
    return response.converter_id
  }, [])

  const handleTabSelect = useCallback((_: unknown, data: { value: unknown }) => {
    const newTab = data.value as string
    setActiveTab(newTab)
    setSelectedConverterType('')
    setQuery('')
    setParamValues({})
    setPreviewOutput('')
    setPreviewOutputType('text')
    setPreviewConverterInstanceId(null)
    setPreviewError(null)
    setShowValidation(false)
    cachedInstanceRef.current = null
  }, [])

  const handleConverterSelect = useCallback((type: string, text: string) => {
    setSelectedConverterType(type)
    setQuery(text)
    const newConverter = converters.find((c) => c.converter_type === type)
    const defaults: Record<string, string> = {}
    for (const p of newConverter?.parameters ?? []) {
      if (p.default != null) {
        defaults[p.name] = p.default
      }
    }
    setParamValues(defaults)
    setPreviewOutput('')
    setPreviewOutputType('text')
    setPreviewConverterInstanceId(null)
    setPreviewError(null)
    setShowValidation(false)
  }, [converters])

  const handleFileBrowse = useCallback((paramName: string) => {
    const input = document.createElement('input')
    input.type = 'file'
    input.onchange = () => {
      const file = input.files?.[0]
      if (file) {
        const reader = new FileReader()
        reader.onload = () => {
          setParamValues((prev) => ({ ...prev, [paramName]: reader.result as string }))
        }
        reader.readAsDataURL(file)
      }
    }
    input.click()
  }, [])

  const handleParamChange = useCallback((name: string, value: string) => {
    setParamValues((prev) => ({ ...prev, [name]: value }))
  }, [])

  const handlePreview = useCallback(async () => {
    const previewValue = effectiveActiveTab === 'text' ? previewText : (attachmentData[effectiveActiveTab] ?? '')
    if (!selectedConverterType || !previewValue.trim()) {
      return
    }
    if (missingRequiredParams.length) {
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
      setPreviewOutputType(previewResponse.converted_value_data_type ?? 'text')
      setPreviewConverterInstanceId(converterId)
    } catch (err) {
      setPreviewError(toApiError(err).detail)
    } finally {
      setIsPreviewing(false)
    }
  }, [effectiveActiveTab, previewText, attachmentData, selectedConverterType, missingRequiredParams, paramValues, activeDataType, getOrCreateConverterInstance])

  // Auto-preview is only safe for converters that:
  //   - aren't LLM-based (network cost)
  //   - emit text output (file outputs would litter the disk on every keystroke)
  // Everything else requires an explicit Preview click.
  const supportsAutoPreview = useMemo(() => {
    if (!selectedConverter) return false
    if (selectedConverter.is_llm_based) return false
    const outputs = selectedConverter.supported_output_types ?? []
    if (outputs.length === 0) return true
    return outputs.every((t) => t === 'text')
  }, [selectedConverter])

  const autoPreviewTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Clear preview when input is emptied (e.g. after sending). Uses the "adjust
  // state during render" pattern with a prev-value comparison so the reset
  // fires exactly once per input transition, even if the input has been empty
  // before (e.g. user types, clears, types, clears again). Avoids
  // react-hooks/set-state-in-effect.
  const currentInput = effectiveActiveTab === 'text' ? previewText : (attachmentData[effectiveActiveTab] ?? '')
  const [prevInput, setPrevInput] = useState(currentInput)
  if (currentInput !== prevInput) {
    setPrevInput(currentInput)
    if (!currentInput.trim()) {
      setPreviewOutput('')
      setPreviewOutputType('text')
      setPreviewConverterInstanceId(null)
      setPreviewError(null)
    }
  }

  useEffect(() => {
    if (autoPreviewTimer.current) {
      clearTimeout(autoPreviewTimer.current)
      autoPreviewTimer.current = null
    }

    if (
      !selectedConverter ||
      !supportsAutoPreview ||
      !currentInput.trim() ||
      missingRequiredParams.length
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
    }, [currentInput, missingRequiredParams, selectedConverter, supportsAutoPreview, handlePreview])
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
            Select and preview converters here in the next step.
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
          selectedValue={effectiveActiveTab}
          onTabSelect={handleTabSelect}
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
            <SelectConverterInput
              query={query}
              selectedConverterType={selectedConverterType}
              groupedConverters={groupedConverters}
              onOptionSelect={handleConverterSelect}
              onQueryChange={setQuery}
            />
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
                  {selectedConverter.supported_input_types.map((t) => (
                    <span key={t} className={`${styles.typeBadge} ${styles[`input_${t}` as keyof typeof styles] ?? ''}`}>
                      {t.replace('_path', '')}
                    </span>
                  ))}
                </div>
                <div className={styles.metaRow}>
                  <Text size={200} className={styles.badgeText}>Out:</Text>
                  {selectedConverter.supported_output_types.map((t) => (
                    <span key={t} className={`${styles.typeBadge} ${styles[`output_${t}` as keyof typeof styles] ?? ''}`}>
                      {t.replace('_path', '')}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {selectedConverter && (
              <ConverterParams
                converter={selectedConverter}
                paramValues={paramValues}
                paramsExpanded={paramsExpanded}
                showValidation={showValidation}
                onParamChange={handleParamChange}
                onFileBrowse={handleFileBrowse}
                onToggleExpanded={() => setParamsExpanded((prev) => !prev)}
              />
            )}

            <ConverterPreview
              activeTab={effectiveActiveTab}
              previewText={previewText}
              attachmentData={attachmentData}
              selectedConverterType={selectedConverterType}
              isPreviewing={isPreviewing}
              previewError={previewError}
              previewOutput={previewOutput}
              previewOutputType={previewOutputType}
              previewConverterInstanceId={previewConverterInstanceId}
              onPreview={handlePreview}
              onUseConvertedValue={onUseConvertedValue}
            />
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
