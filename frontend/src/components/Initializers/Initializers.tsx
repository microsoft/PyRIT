import { useEffect, useState } from 'react'

import { Button, MessageBar, MessageBarBody, Spinner, Tab, TabList, Text } from '@fluentui/react-components'
import type { SelectTabData, SelectTabEvent } from '@fluentui/react-components'
import { ArrowSyncRegular } from '@fluentui/react-icons'

import { initializersApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type {
  CustomInitializer,
  InitializerSettingsResponse,
  RegisteredInitializer,
  UpdateAdditionalInitializerRequest,
} from '@/types'

import AdditionalInitializers from './AdditionalInitializers'
import AvailableInitializersDialog from './AvailableInitializersDialog'
import BaselineInitializers from './BaselineInitializers'
import CustomInitializers from './CustomInitializers'
import { useInitializersStyles } from './Initializers.styles'

interface StatusMessage {
  intent: 'success' | 'error'
  text: string
}

type InitializerTab = 'startup' | 'custom'

const EMPTY_SETTINGS: InitializerSettingsResponse = {
  baseline: [],
  additional: [],
}

export default function Initializers() {
  const styles = useInitializersStyles()
  const [settings, setSettings] = useState<InitializerSettingsResponse>(EMPTY_SETTINGS)
  const [registeredInitializers, setRegisteredInitializers] = useState<RegisteredInitializer[]>([])
  const [customInitializers, setCustomInitializers] = useState<CustomInitializer[]>([])
  const [selectedTab, setSelectedTab] = useState<InitializerTab>('startup')
  const [loading, setLoading] = useState(true)
  const [statusMessage, setStatusMessage] = useState<StatusMessage | null>(null)
  const [refetchCount, setRefetchCount] = useState(0)
  const [creating, setCreating] = useState(false)
  const [savingInitializerId, setSavingInitializerId] = useState<string | null>(null)
  const [saveErrors, setSaveErrors] = useState<Record<string, string>>({})
  const [applyingInitializerId, setApplyingInitializerId] = useState<string | null>(null)
  const [deletingInitializerId, setDeletingInitializerId] = useState<string | null>(null)
  const [registeringCustom, setRegisteringCustom] = useState(false)
  const [deletingCustomName, setDeletingCustomName] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    const loadInitializersAsync = async (): Promise<void> => {
      const [settingsResult, registeredResult, customResult] = await Promise.allSettled([
        initializersApi.getSettings(),
        initializersApi.listRegistered(),
        initializersApi.listCustom(),
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

      if (customResult.status === 'fulfilled') {
        setCustomInitializers(customResult.value)
      } else {
        setStatusMessage({ intent: 'error', text: toApiError(customResult.reason).detail })
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

  const refetchCustomCatalog = async (): Promise<void> => {
    const [custom, registered] = await Promise.all([
      initializersApi.listCustom(),
      initializersApi.listRegistered(),
    ])
    setCustomInitializers(custom)
    setRegisteredInitializers(registered.items)
  }

  const handleTabSelect = (_: SelectTabEvent, data: SelectTabData): void => {
    if (data.value === 'startup' || data.value === 'custom') {
      setSelectedTab(data.value)
    }
  }

  const handleRegisterCustom = async (name: string, scriptContent: string): Promise<boolean> => {
    setRegisteringCustom(true)
    try {
      await initializersApi.register({ name, script_content: scriptContent })
      await refetchCustomCatalog()
      setStatusMessage({ intent: 'success', text: `Registered ${name}.` })
      return true
    } catch (error) {
      setStatusMessage({ intent: 'error', text: toApiError(error).detail })
      return false
    } finally {
      setRegisteringCustom(false)
    }
  }

  const handleDeleteCustom = async (name: string): Promise<void> => {
    setDeletingCustomName(name)
    try {
      await initializersApi.unregister(name)
      await refetchCustomCatalog()
      setStatusMessage({ intent: 'success', text: `Removed ${name}.` })
    } catch (error) {
      setStatusMessage({ intent: 'error', text: toApiError(error).detail })
    } finally {
      setDeletingCustomName(null)
    }
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
            Manage startup initializer invocations and persisted custom initializer definitions.
          </Text>
        </div>
        <div className={styles.headerActions}>
          {selectedTab === 'startup' && (
            <AvailableInitializersDialog
              registeredInitializers={registeredInitializers}
              disabled={loading}
            />
          )}
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

      <TabList selectedValue={selectedTab} onTabSelect={handleTabSelect}>
        <Tab value="startup">Startup</Tab>
        <Tab value="custom">Custom</Tab>
      </TabList>

      {loading ? (
        <div className={styles.loadingState}>
          <Spinner label="Loading initializer settings..." />
        </div>
      ) : (
        selectedTab === 'startup' ? (
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
        ) : (
          <CustomInitializers
            items={customInitializers}
            registering={registeringCustom}
            deletingName={deletingCustomName}
            onRegister={handleRegisterCustom}
            onDelete={handleDeleteCustom}
          />
        )
      )}
    </main>
  )
}
