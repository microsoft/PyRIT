import { useEffect, useState } from 'react'
import {
  Button,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
  DialogTrigger,
  MessageBar,
  MessageBarBody,
  Select,
  Spinner,
  Text,
} from '@fluentui/react-components'
import { AddRegular, ArrowSyncRegular, AppsListRegular } from '@fluentui/react-icons'

import { initializersApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type {
  BaselineInitializerSetting,
  InitializerSettingsResponse,
  RegisteredInitializer,
  UpdateAdditionalInitializerRequest,
} from '@/types'

import InitializerList from './InitializerList'
import InitializerParametersDialog from './InitializerParametersDialog'
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

function formatSupportedParameterSummary(initializer: RegisteredInitializer): string[] {
  if (initializer.supported_parameters.length === 0) {
    return ['No declared parameters.']
  }

  return initializer.supported_parameters.map((parameter) => {
    const requiredLabel = parameter.required ? 'required' : 'optional'
    return `${parameter.name} (${parameter.type_name}, ${requiredLabel})`
  })
}

export default function InitializerConfig() {
  const styles = useInitializerConfigStyles()
  const [settings, setSettings] = useState<InitializerSettingsResponse>(EMPTY_SETTINGS)
  const [registeredInitializers, setRegisteredInitializers] = useState<RegisteredInitializer[]>([])
  const [loading, setLoading] = useState(true)
  const [statusMessage, setStatusMessage] = useState<StatusMessage | null>(null)
  const [refetchCount, setRefetchCount] = useState(0)
  const [creating, setCreating] = useState(false)
  const [selectedAddInitializerName, setSelectedAddInitializerName] = useState('')
  const [addDialogOpen, setAddDialogOpen] = useState(false)
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

  const handleAdd = async (parameters: Record<string, unknown> | null): Promise<void> => {
    if (!initializerToAdd) {
      return
    }
    setCreating(true)
    try {
      await initializersApi.createAdditional({ initializer_name: initializerToAdd, parameters })
      setStatusMessage({ intent: 'success', text: `Added ${initializerToAdd} initializer.` })
      setAddDialogOpen(false)
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

  const addableInitializers = registeredInitializers
  const initializerToAdd = selectedAddInitializerName || addableInitializers[0]?.initializer_name || ''
  const selectedInitializer =
    addableInitializers.find((initializer) => initializer.initializer_name === initializerToAdd) ?? null

  return (
    <main className={styles.root} data-testid="initializer-config">
      <div className={styles.header}>
        <div className={styles.headerText}>
          <Text as="h1" size={600} weight="semibold">Initializers</Text>
          <Text size={300}>
            Browse every registered initializer, review the read-only baseline that ran at startup, and manage
            additional initializer invocations that run after it.
          </Text>
        </div>
        <div className={styles.headerActions}>
          <Dialog>
            <DialogTrigger disableButtonEnhancement>
              <Button appearance="secondary" icon={<AppsListRegular />} disabled={loading}>
                Browse available initializers
              </Button>
            </DialogTrigger>
            <DialogSurface>
              <DialogBody>
                <DialogTitle>Available initializers</DialogTitle>
                <DialogContent>
                  <Text size={300} className={styles.metadataText}>
                    Every initializer registered with PyRIT. This is a read-only reference of what exists and the
                    parameters each one accepts.
                  </Text>
                  {registeredInitializers.length === 0 ? (
                    <Text className={styles.emptyState}>No registered initializers were found.</Text>
                  ) : (
                    <div
                      className={styles.dialogList}
                      role="list"
                      aria-label="Available initializers"
                    >
                      {registeredInitializers.map((initializer: RegisteredInitializer) => (
                        <div
                          key={initializer.initializer_name}
                          className={styles.baselineCard}
                          role="listitem"
                          data-testid={`available-initializer-row-${initializer.initializer_name}`}
                        >
                          <div className={styles.titleGroup}>
                            <Text weight="semibold" size={400}>{initializer.initializer_name}</Text>
                            <Text size={300}>{initializer.description || 'No description available.'}</Text>
                            <Text size={200} className={styles.metadataText}>
                              Required env vars: {initializer.required_env_vars.length > 0
                                ? initializer.required_env_vars.join(', ')
                                : 'None'}
                            </Text>
                          </div>
                          <div>
                            <Text weight="semibold" size={300}>Parameters</Text>
                            <div className={styles.parameterSummaryList}>
                              {formatSupportedParameterSummary(initializer).map((summary: string) => (
                                <Text key={summary} size={200} className={styles.metadataText}>
                                  {summary}
                                </Text>
                              ))}
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </DialogContent>
                <DialogActions>
                  <DialogTrigger disableButtonEnhancement>
                    <Button appearance="secondary">Close</Button>
                  </DialogTrigger>
                </DialogActions>
              </DialogBody>
            </DialogSurface>
          </Dialog>
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
              <div className={styles.baselineGroup} role="list" aria-label="Baseline initializers">
                {settings.baseline.map((item: BaselineInitializerSetting) => {
                  const initializerName = item.initializer.initializer_name
                  return (
                    <div
                      key={`${initializerName}:${item.order_index}`}
                      className={styles.baselineGroupItem}
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
              <Select
                aria-label="Initializer to add"
                className={styles.addInitializerSelect}
                value={initializerToAdd}
                disabled={creating || addableInitializers.length === 0}
                onChange={(_event, data) => setSelectedAddInitializerName(data.value)}
              >
                {addableInitializers.map((initializer: RegisteredInitializer) => (
                  <option key={initializer.initializer_name} value={initializer.initializer_name}>
                    {initializer.initializer_name}
                  </option>
                ))}
              </Select>
              <Button
                appearance="primary"
                icon={<AddRegular />}
                onClick={() => setAddDialogOpen(true)}
                disabled={creating || !initializerToAdd}
              >
                {creating ? 'Adding...' : 'Add initializer'}
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

            <InitializerParametersDialog
              open={addDialogOpen}
              mode="add"
              initializer={selectedInitializer}
              initialParameters={null}
              submitting={creating}
              onSubmit={handleAdd}
              onOpenChange={setAddDialogOpen}
            />
          </section>
        </>
      )}
    </main>
  )
}
