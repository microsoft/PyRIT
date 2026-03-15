import { useEffect, useMemo, useState } from 'react'
import { Button, Combobox, Field, Input, MessageBar, MessageBarBody, Option, Select, Spinner, Text } from '@fluentui/react-components'
import { DismissRegular, PlayRegular } from '@fluentui/react-icons'
import { convertersApi } from '../../services/api'
import { toApiError } from '../../services/errors'
import type { ConverterCatalogEntry } from '../../types'
import { useConverterPanelStyles } from './ConverterPanel.styles'

interface ConverterPanelProps {
  onClose: () => void
  previewText?: string
  onUseConvertedValue?: (original: string, converted: string) => void
}

export default function ConverterPanel({ onClose, previewText = '', onUseConvertedValue }: ConverterPanelProps) {
  const styles = useConverterPanelStyles()
  const [converters, setConverters] = useState<ConverterCatalogEntry[]>([])
  const [selectedConverterType, setSelectedConverterType] = useState('')
  const [query, setQuery] = useState('')
  const [paramValues, setParamValues] = useState<Record<string, string>>({})
  const [previewOutput, setPreviewOutput] = useState('')
  const [isPreviewing, setIsPreviewing] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)
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
        const first = response.items[0]?.converter_type || ''
        setSelectedConverterType((current) => current || first)
        setQuery((current) => current || first)
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

  const filteredConverters = useMemo(() => {
    if (query === selectedConverterType) {
      return converters
    }
    return converters.filter((c) => c.converter_type.toLowerCase().includes(query.toLowerCase()))
  }, [converters, query, selectedConverterType])

  const selectedConverter = converters.find(
    (converter) => converter.converter_type === selectedConverterType
  ) ?? converters[0]

  const handlePreview = async () => {
    if (!selectedConverterType || !previewText.trim()) {
      return
    }
    setIsPreviewing(true)
    setPreviewError(null)
    setPreviewOutput('')

    try {
      const createResponse = await convertersApi.createConverter({
        type: selectedConverterType,
        params: { ...paramValues },
      })

      const previewResponse = await convertersApi.previewConversion({
        original_value: previewText,
        converter_ids: [createResponse.converter_id],
      })

      setPreviewOutput(previewResponse.converted_value)
    } catch (err) {
      setPreviewError(toApiError(err).detail)
    } finally {
      setIsPreviewing(false)
    }
  }

  return (
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
                }}
                onChange={(e) => setQuery((e.target as HTMLInputElement).value)}
                placeholder="Search converters..."
                data-testid="converter-panel-select"
              >
                {filteredConverters.map((converter) => (
                  <Option key={converter.converter_type} value={converter.converter_type} text={converter.converter_type}>
                    {converter.converter_type}
                    {converter.is_llm_based && <span className={styles.llmBadge}>LLM</span>}
                  </Option>
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
                <div className={styles.metaRow}>
                  <Text size={200} className={styles.badgeText}>
                    In: {selectedConverter.supported_input_types.join(', ') || 'n/a'}
                  </Text>
                </div>
                <div className={styles.metaRow}>
                  <Text size={200} className={styles.badgeText}>
                    Out: {selectedConverter.supported_output_types.join(', ') || 'n/a'}
                  </Text>
                </div>
              </div>
            )}

            {selectedConverter && (selectedConverter.parameters?.length ?? 0) > 0 && (
              <div className={styles.paramsSection} data-testid="converter-params">
                <Text weight="semibold" size={300}>Parameters</Text>
                {(selectedConverter.parameters ?? []).map((param) => (
                  <Field
                    key={param.name}
                    label={`${param.name}${param.required ? ' *' : ''}`}
                    hint={param.type_name}
                  >
                    {param.choices ? (
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
                    ) : (
                      <Input
                        value={paramValues[param.name] ?? ''}
                        placeholder={param.default_value ?? undefined}
                        onChange={(_, data) =>
                          setParamValues((prev) => ({ ...prev, [param.name]: data.value }))
                        }
                        data-testid={`param-${param.name}`}
                      />
                    )}
                  </Field>
                ))}
              </div>
            )}

            <div className={styles.outputSection} data-testid="converter-preview-section">
              <Button
                appearance="primary"
                size="small"
                icon={isPreviewing ? <Spinner size="tiny" /> : <PlayRegular />}
                onClick={handlePreview}
                disabled={isPreviewing || !previewText.trim() || !selectedConverterType}
                data-testid="converter-preview-btn"
              >
                {isPreviewing ? 'Converting...' : 'Preview'}
              </Button>

              {!previewText.trim() && (
                <Text size={200} className={styles.hintText}>
                  Type in the chat input box to preview a conversion.
                </Text>
              )}

              {previewError && (
                <MessageBar intent="error" data-testid="converter-preview-error">
                  <MessageBarBody>{previewError}</MessageBarBody>
                </MessageBar>
              )}

              <div data-testid="converter-output">
                <Text weight="semibold" size={300}>Output</Text>
                <div className={styles.outputBox}>
                  {previewOutput ? (
                    <pre className={styles.previewPre} data-testid="converter-preview-result">{previewOutput}</pre>
                  ) : (
                    <Text size={200} className={styles.hintText}>
                      Converted output will appear here.
                    </Text>
                  )}
                </div>
              </div>

              {previewOutput && (
                <Button
                  appearance="primary"
                  size="small"
                  onClick={() => onUseConvertedValue?.(previewText, previewOutput)}
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
  )
}
