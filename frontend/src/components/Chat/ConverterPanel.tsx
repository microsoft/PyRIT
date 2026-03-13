import { useEffect, useMemo, useState } from 'react'
import { Button, Combobox, Field, MessageBar, MessageBarBody, Option, Spinner, Text } from '@fluentui/react-components'
import { DismissRegular } from '@fluentui/react-icons'
import { convertersApi } from '../../services/api'
import { toApiError } from '../../services/errors'
import type { ConverterCatalogEntry } from '../../types'
import { useConverterPanelStyles } from './ConverterPanel.styles'

interface ConverterPanelProps {
  onClose: () => void
}

export default function ConverterPanel({ onClose }: ConverterPanelProps) {
  const styles = useConverterPanelStyles()
  const [converters, setConverters] = useState<ConverterCatalogEntry[]>([])
  const [selectedConverterType, setSelectedConverterType] = useState('')
  const [query, setQuery] = useState('')
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
    // Show all options when query matches the selected converter (no active filter)
    if (query === selectedConverterType) {
      return converters
    }
    return converters.filter((c) => c.converter_type.toLowerCase().includes(query.toLowerCase()))
  }, [converters, query, selectedConverterType])

  const selectedConverter = converters.find(
    (converter) => converter.converter_type === selectedConverterType
  ) ?? converters[0]

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
                  setSelectedConverterType(data.optionValue ?? '')
                  setQuery(data.optionText ?? '')
                }}
                onChange={(e) => setQuery((e.target as HTMLInputElement).value)}
                placeholder="Search converters..."
                data-testid="converter-panel-select"
              >
                {filteredConverters.map((converter) => (
                  <Option key={converter.converter_type} value={converter.converter_type}>
                    {converter.converter_type}
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

            <div className={styles.outputSection} data-testid="converter-output">
              <Text weight="semibold" size={300}>Output</Text>
              <div className={styles.outputBox}>
                <Text size={200} className={styles.hintText}>
                  Converted output will appear here.
                </Text>
              </div>
            </div>
          </div>
        )}
      </div>
    </aside>
  )
}
