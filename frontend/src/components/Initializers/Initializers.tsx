import { useEffect, useState } from 'react'

import { Button, MessageBar, MessageBarBody, Spinner, Text } from '@fluentui/react-components'
import { ArrowSyncRegular } from '@fluentui/react-icons'

import { initializersApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { InitializerSettingsResponse, RegisteredInitializer, UpdateAdditionalInitializerRequest } from '@/types'

import AdditionalInitializers from './AdditionalInitializers'
import AvailableInitializersDialog from './AvailableInitializersDialog'
import BaselineInitializers from './BaselineInitializers'
import { useInitializersStyles } from './Initializers.styles'

interface StatusMessage {
  intent: 'success' | 'error'
  text: string
}

const EMPTY_SETTINGS: InitializerSettingsResponse = {
  baseline: [],
  additional: [],
}

export default function Initializers() {
  const styles = useInitializersStyles()
  const [settings, setSettings] = useState<InitializerSettingsResponse>(EMPTY_SETTINGS)
  const [registeredInitializers, setRegisteredInitializers] = useState<RegisteredInitializer[]>([])
  const [loading, setLoading] = useState(true)
  const [statusMessage, setStatusMessage] = useState<StatusMessage | null>(null)
  const [refetchCount, setRefetchCount] = useState(0)
  const [creating, setCreating] = useState(false)
  const [savingInitializerId, setSavingInitializerId] = useState<string | null>(null)
  const [saveErrors, setSaveErrors] = useState<Record<string, string>>({})
  const [applyingInitializerId, setApplyingInitializerId] = useState<string | null>(null)
  const [deletingInitializerId, setDeletingInitializerId] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    const loadInitializersAsync = async (): Promise<void> => {
      const [settingsResult, registeredResult] = await Promise.allSettled([
        initializersApi.getSettings(),
        initializersApi.listRegistered(),
      ])
      if (cancelled) {
        return
      }

      if (settingsResult.status === 'fulfilled') {
        setSettings(settingsResult.value)
      } else {
        setStatusMessage({ intent: 'error', text: toApiError(settingsResult.reason).detail })
      }

      if (registeredResult.status === 'fulfilled') {
        setRegisteredInitializers(registeredResult.value.items)
      } else {
        const catalogError = toApiError(registeredResult.reason).detail
        setStatusMessage((current: StatusMessage | null) =>
          current
            ? { intent: 'error', text: `${current.text} ${catalogError}` }
            : { intent: 'error', text: catalogError },
        )
      }

      setLoading(false)
    }

    void loadInitializersAsync()
    return () => {
      cancelled = true
    }
  }, [refetchCount])

  const refreshSettings = (): void => {
    setLoading(true)
    setStatusMessage(null)
    setRefetchCount((currentCount: number) => currentCount + 1)
  }

  const refetchSettingsOnly = async (): Promise<void> => {
    const response = await initializersApi.getSettings()
    setSettings(response)
  }

  const handleAdd = async (
    initializerName: string,
    parameters: Record<string, unknown> | null,
  ): Promise<boolean> => {
    setCreating(true)
    try {
      await initializersApi.createAdditional({ initializer_name: initializerName, parameters })
      setStatusMessage({ intent: 'success', text: `Added ${initializerName} initializer.` })
      await refetchSettingsOnly()
      return true
    } catch (error) {
      const detail = toApiError(error).detail
      setStatusMessage({ intent: 'error', text: detail })
      throw error
    } finally {
      setCreating(false)
    }
  }

  const handleSave = async (
    id: string,
    request: UpdateAdditionalInitializerRequest,
  ): Promise<boolean> => {
    setSavingInitializerId(id)
    setSaveErrors((currentErrors) => {
      const remainingErrors = { ...currentErrors }
      delete remainingErrors[id]
      return remainingErrors
    })
    try {
      await initializersApi.updateAdditional(id, request)
      setStatusMessage({ intent: 'success', text: 'Saved additional initializer.' })
      await refetchSettingsOnly()
      return true
    } catch (error) {
      const detail = toApiError(error).detail
      setStatusMessage({ intent: 'error', text: detail })
      setSaveErrors((currentErrors) => ({ ...currentErrors, [id]: detail }))
      return false
    } finally {
      setSavingInitializerId(null)
    }
  }

  const clearSaveError = (id: string): void => {
    setSaveErrors((currentErrors) => {
      const remainingErrors = { ...currentErrors }
      delete remainingErrors[id]
      return remainingErrors
    })
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

  return (
    <main className={styles.root} data-testid="initializers">
      <div className={styles.header}>
        <div className={styles.headerText}>
          <Text as="h1" size={600} weight="semibold">Initializers</Text>
          <Text size={300}>
            Browse every registered initializer, review the read-only baseline that ran at startup, and manage
            additional initializer invocations that run after it.
          </Text>
        </div>
        <div className={styles.headerActions}>
          <AvailableInitializersDialog
            registeredInitializers={registeredInitializers}
            disabled={loading}
          />
          <Button
            appearance="subtle"
            icon={<ArrowSyncRegular />}
            className={styles.touchTarget}
            onClick={refreshSettings}
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
          <BaselineInitializers
            items={settings.baseline}
            registeredInitializers={registeredInitializers}
          />
          <AdditionalInitializers
            items={settings.additional}
            registeredInitializers={registeredInitializers}
            creating={creating}
            savingInitializerId={savingInitializerId}
            saveErrors={saveErrors}
            applyingInitializerId={applyingInitializerId}
            deletingInitializerId={deletingInitializerId}
            onAdd={handleAdd}
            onSave={handleSave}
            onClearSaveError={clearSaveError}
            onApply={handleApply}
            onRemove={handleRemove}
          />
        </>
      )}
    </main>
  )
}
