import { useEffect, useState } from 'react'
import { Badge, Button, MessageBar, MessageBarBody, Spinner, Text } from '@fluentui/react-components'
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
            <Text size={300}>No converters are currently registered.</Text>
            <Text size={200} className={styles.hintText}>
              Once the backend registry has converter instances, they will appear here.
            </Text>
          </div>
        )}

        {!isLoading && !error && converters.length > 0 && (
          <div className={styles.converterList} data-testid="converter-panel-list">
            {converters.map((converter) => (
              <div
                key={converter.converter_type}
                className={styles.converterCard}
                data-testid={`converter-item-${converter.converter_type}`}
              >
                <div className={styles.converterTitleRow}>
                  <Text weight="semibold" size={300} className={styles.converterName}>
                    {converter.converter_type}
                  </Text>
                  <Badge appearance="outline">Available</Badge>
                </div>
                <div className={styles.metaRow}>
                  <Text size={200} className={styles.badgeText}>
                    In: {converter.supported_input_types.join(', ') || 'n/a'}
                  </Text>
                </div>
                <div className={styles.metaRow}>
                  <Text size={200} className={styles.badgeText}>
                    Out: {converter.supported_output_types.join(', ') || 'n/a'}
                  </Text>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </aside>
  )
}
