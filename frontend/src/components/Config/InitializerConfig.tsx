import { useEffect, useState } from 'react'
import {
  Button,
  Combobox,
  Field,
  MessageBar,
  MessageBarBody,
  Option,
  Spinner,
  Text,
} from '@fluentui/react-components'
import { AddRegular, ArrowSyncRegular } from '@fluentui/react-icons'

import { initializersApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type {
  BaselineInitializerSetting,
  InitializerSettingsResponse,
  RegisteredInitializer,
  UpdateAdditionalInitializerRequest,
} from '@/types'

import InitializerList from './InitializerList'
import { useInitializerConfigStyles } from './InitializerConfig.styles'

interface StatusMessage {
  intent: 'success' | 'error'
  text: string
}

const EMPTY_SETTINGS: InitializerSettingsResponse = {
  baseline: [],
  additional: [],
}

function formatParameters(parameters?: Record<string, unknown> | null): string {
  return JSON.stringify(parameters ?? {}, null, 2)
}

export default function InitializerConfig() {
  const styles = useInitializerConfigStyles()
  const [settings, setSettings] = useState<InitializerSettingsResponse>(EMPTY_SETTINGS)
  const [registeredInitializers, setRegisteredInitializers] = useState<RegisteredInitializer[]>([])
  const [selectedInitializerName, setSelectedInitializerName] = useState('')
  const [loading, setLoading] = useState(true)
  const [statusMessage, setStatusMessage] = useState<StatusMessage | null>(null)
  const [refetchCount, setRefetchCount] = useState(0)
  const [creating, setCreating] = useState(false)
  const [savingInitializerId, setSavingInitializerId] = useState<string | null>(null)
  const [applyingInitializerId, setApplyingInitializerId] = useState<string | null>(null)
  const [deletingInitializerId, setDeletingInitializerId] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    const loadInitializersAsync = async (): Promise<void> => {
      try {
        const [settingsResponse, registeredResponse] = await Promise.all([
          initializersApi.getSettings(),
          initializersApi.listRegistered(),
        ])
        if (cancelled) {
          return
        }
        setSettings(settingsResponse)
        setRegisteredInitializers(registeredResponse.items)
      } catch (error) {
        if (cancelled) {
          return
        }
        setStatusMessage({ intent: 'error', text: toApiError(error).detail })
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    void loadInitializersAsync()
    return () => {
      cancelled = true
    }
  }, [refetchCount])

  const refreshSettings = (clearStatusMessage: boolean = true): void => {
    setLoading(true)
    if (clearStatusMessage) {
      setStatusMessage(null)
    }
    setRefetchCount((currentCount: number) => currentCount + 1)
  }

  const refetchSettingsOnly = async (): Promise<void> => {
    const response = await initializersApi.getSettings()
    setSettings(response)
  }

  const handleAdd = async (): Promise<void> => {
    if (!selectedInitializerName) {
      return
    }

    setCreating(true)
    try {
      await initializersApi.createAdditional({ initializer_name: selectedInitializerName })
      setStatusMessage({ intent: 'success', text: `Added ${selectedInitializerName}.` })
      setSelectedInitializerName('')
      await refetchSettingsOnly()
    } catch (error) {
      setStatusMessage({ intent: 'error', text: toApiError(error).detail })
    } finally {
      setCreating(false)
    }
  }

  const handleSave = async (
    id: string,
    request: UpdateAdditionalInitializerRequest,
  ): Promise<void> => {
    setSavingInitializerId(id)
    try {
      await initializersApi.updateAdditional(id, request)
      setStatusMessage({ intent: 'success', text: 'Saved additional initializer.' })
      await refetchSettingsOnly()
    } catch (error) {
      setStatusMessage({ intent: 'error', text: toApiError(error).detail })
    } finally {
      setSavingInitializerId(null)
    }
  }

  const handleApply = async (
    id: string,
    initializerName: string,
    parameters?: Record<string, unknown> | null,
  ): Promise<void> => {
    setApplyingInitializerId(id)
    try {
      await initializersApi.applyNow(initializerName, { parameters })
      setStatusMessage({ intent: 'success', text: `Applied ${initializerName}.` })
    } catch (error) {
      setStatusMessage({ intent: 'error', text: toApiError(error).detail })
    } finally {
      setApplyingInitializerId(null)
    }
  }

  const handleBaselineApply = async (item: BaselineInitializerSetting): Promise<void> => {
    const initializerName = item.initializer.initializer_name
    setApplyingInitializerId(`baseline:${initializerName}:${item.order_index}`)
    try {
      await initializersApi.applyNow(initializerName, { parameters: item.parameters ?? {} })
      setStatusMessage({ intent: 'success', text: `Applied ${initializerName}.` })
    } catch (error) {
      setStatusMessage({ intent: 'error', text: toApiError(error).detail })
    } finally {
      setApplyingInitializerId(null)
    }
  }

  const handleRemove = async (id: string): Promise<void> => {
    setDeletingInitializerId(id)
    try {
      await initializersApi.deleteAdditional(id)
      setStatusMessage({ intent: 'success', text: 'Removed additional initializer.' })
      await refetchSettingsOnly()
    } catch (error) {
      setStatusMessage({ intent: 'error', text: toApiError(error).detail })
    } finally {
      setDeletingInitializerId(null)
    }
  }

  const hasNoInitializers = settings.baseline.length === 0 && settings.additional.length === 0
  const canAddSelectedInitializer = registeredInitializers.some(
    (initializer: RegisteredInitializer) => initializer.initializer_name === selectedInitializerName,
  )

  return (
    <main className={styles.root} data-testid="initializer-config">
      <div className={styles.header}>
        <div className={styles.headerText}>
          <Text as="h1" size={600} weight="semibold">Initializers</Text>
          <Text size={300}>
            Review read-only baseline initializers and manage additional initializer invocations that run after them.
          </Text>
        </div>
        <div className={styles.headerActions}>
          <Button
            appearance="subtle"
            icon={<ArrowSyncRegular />}
            onClick={() => refreshSettings()}
            disabled={loading}
          >
            Refresh
          </Button>
        </div>
      </div>

      {statusMessage && (
        <MessageBar intent={statusMessage.intent} className={styles.message}>
          <MessageBarBody>{statusMessage.text}</MessageBarBody>
        </MessageBar>
      )}

      {loading ? (
        <div className={styles.loadingState}>
          <Spinner label="Loading initializer settings..." />
        </div>
      ) : (
        <>
          {hasNoInitializers && (
            <Text className={styles.emptyState}>No initializer settings are available.</Text>
          )}

          <section className={styles.section} aria-labelledby="baseline-initializers-heading">
            <div className={styles.sectionHeader}>
              <Text as="h2" id="baseline-initializers-heading" size={500} weight="semibold">
                Baseline initializers
              </Text>
              <Text size={300} className={styles.metadataText}>
                Read-only initializers from the .pyrit_conf baseline.
              </Text>
            </div>
            {settings.baseline.length === 0 ? (
              <Text className={styles.emptyState}>No baseline initializers are configured.</Text>
            ) : (
              <div className={styles.baselineList} role="list" aria-label="Baseline initializers">
                {settings.baseline.map((item: BaselineInitializerSetting) => {
                  const initializerName = item.initializer.initializer_name
                  const baselineApplyId = `baseline:${initializerName}:${item.order_index}`
                  return (
                    <div
                      key={`${initializerName}:${item.order_index}`}
                      className={styles.baselineCard}
                      role="listitem"
                      data-testid={`baseline-initializer-row-${initializerName}`}
                    >
                      <div className={styles.baselineHeader}>
                        <div className={styles.titleGroup}>
                          <Text weight="semibold" size={400}>{initializerName}</Text>
                          <Text size={300}>{item.initializer.description || 'No description available.'}</Text>
                          <Text size={200} className={styles.metadataText}>
                            Required env vars: {item.initializer.required_env_vars.length > 0
                              ? item.initializer.required_env_vars.join(', ')
                              : 'None'}
                          </Text>
                          <Text size={200} className={styles.metadataText}>Order: {item.order_index}</Text>
                        </div>
                        <Button
                          appearance="secondary"
                          onClick={() => void handleBaselineApply(item)}
                          disabled={applyingInitializerId === baselineApplyId}
                        >
                          {applyingInitializerId === baselineApplyId ? 'Applying...' : 'Apply now'}
                        </Button>
                      </div>
                      <div>
                        <Text weight="semibold" size={300}>Parameters</Text>
                        <pre className={styles.parametersBlock}>{formatParameters(item.parameters)}</pre>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </section>

          <section className={styles.section} aria-labelledby="additional-initializers-heading">
            <div className={styles.sectionHeader}>
              <Text as="h2" id="additional-initializers-heading" size={500} weight="semibold">
                Additional initializers
              </Text>
              <Text size={300} className={styles.metadataText}>
                Add and edit initializer invocations that run after the baseline.
              </Text>
            </div>

            <div className={styles.addInitializerRow}>
              <Field label="Add initializer">
                <Combobox
                  className={styles.initializerPicker}
                  value={selectedInitializerName}
                  selectedOptions={selectedInitializerName ? [selectedInitializerName] : []}
                  onOptionSelect={(_, data) => {
                    setSelectedInitializerName(data.optionValue ?? '')
                  }}
                  onChange={(event) => setSelectedInitializerName(event.target.value)}
                  placeholder="Select an initializer"
                  disabled={creating || registeredInitializers.length === 0}
                >
                  {registeredInitializers.map((initializer: RegisteredInitializer) => (
                    <Option
                      key={initializer.initializer_name}
                      value={initializer.initializer_name}
                      text={initializer.initializer_name}
                    >
                      {initializer.initializer_name}
                    </Option>
                  ))}
                </Combobox>
              </Field>
              <Button
                appearance="primary"
                icon={<AddRegular />}
                onClick={() => void handleAdd()}
                disabled={creating || !canAddSelectedInitializer}
              >
                {creating ? 'Adding...' : 'Add'}
              </Button>
            </div>

            {settings.additional.length === 0 ? (
              <Text className={styles.emptyState}>No additional initializers are configured.</Text>
            ) : (
              <InitializerList
                items={settings.additional}
                savingInitializerId={savingInitializerId}
                applyingInitializerId={applyingInitializerId}
                deletingInitializerId={deletingInitializerId}
                onSave={handleSave}
                onApply={handleApply}
                onRemove={handleRemove}
              />
            )}
          </section>
        </>
      )}
    </main>
  )
}
