import { useEffect, useRef, useState } from 'react'

import { MessageBar, MessageBarBody, Spinner, Text } from '@fluentui/react-components'

import { useRuntime } from '@/hooks/useRuntime'
import { useAutoRefresh } from '@/hooks/useAutoRefresh'
import { initializersApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { InitializerSettingsResponse, RegisteredInitializer } from '@/types'

import AvailableInitializersDialog from './AvailableInitializersDialog'
import ConfiguredInitializers from './ConfiguredInitializers'
import type { CatalogStatus } from './initializerLookup'
import { useInitializersStyles } from './Initializers.styles'

interface StatusMessage {
  intent: 'error'
  text: string
}

const EMPTY_SETTINGS: InitializerSettingsResponse = {
  configured: [],
}

export default function Initializers() {
  const { generation } = useRuntime()
  const styles = useInitializersStyles()
  const [settings, setSettings] = useState<InitializerSettingsResponse>(EMPTY_SETTINGS)
  const [registeredInitializers, setRegisteredInitializers] = useState<RegisteredInitializer[]>([])
  const [catalogStatus, setCatalogStatus] = useState<CatalogStatus>('loading')
  const [loading, setLoading] = useState(true)
  const [statusMessage, setStatusMessage] = useState<StatusMessage | null>(null)
  const [refetchCount, setRefetchCount] = useState(0)
  const pending = useRef(false)

  useEffect(() => {
    let cancelled = false
    pending.current = true

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
      }

      if (registeredResult.status === 'fulfilled') {
        setRegisteredInitializers(registeredResult.value.items)
        setCatalogStatus('loaded')
      } else {
        setCatalogStatus((current) => current === 'loaded' ? current : 'error')
      }

      setLoading(false)
      pending.current = false
      const failures = [
        settingsResult.status === 'rejected' ? toApiError(settingsResult.reason).detail : null,
        registeredResult.status === 'rejected' ? toApiError(registeredResult.reason).detail : null,
      ].filter((detail: string | null): detail is string => detail !== null)
      setStatusMessage(failures.length > 0 ? { intent: 'error', text: failures.join(' ') } : null)
    }

    void loadInitializersAsync()
    return () => {
      cancelled = true
      pending.current = false
    }
  }, [refetchCount, generation])

  const refreshSettings = (): void => {
    if (pending.current) return
    setRefetchCount((currentCount: number) => currentCount + 1)
  }
  useAutoRefresh(refreshSettings, !loading)

  return (
    <section className={styles.root} data-testid="initializers">
      <div className={styles.header}>
        <Text size={300}>
          Browse every registered initializer and review the startup sequence from the active .pyrit_conf.
        </Text>
        <div className={styles.headerActions}>
          <AvailableInitializersDialog
            registeredInitializers={registeredInitializers}
            catalogStatus={catalogStatus}
            disabled={loading || catalogStatus !== 'loaded'}
          />
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
        <ConfiguredInitializers
          items={settings.configured}
          registeredInitializers={registeredInitializers}
          catalogStatus={catalogStatus}
        />
      )}
    </section>
  )
}
