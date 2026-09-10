import { useEffect, useState } from 'react'

import { Button, MessageBar, MessageBarBody, Spinner, Text } from '@fluentui/react-components'
import { ArrowSyncRegular } from '@fluentui/react-icons'

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
  const styles = useInitializersStyles()
  const [settings, setSettings] = useState<InitializerSettingsResponse>(EMPTY_SETTINGS)
  const [registeredInitializers, setRegisteredInitializers] = useState<RegisteredInitializer[]>([])
  const [catalogStatus, setCatalogStatus] = useState<CatalogStatus>('loading')
  const [loading, setLoading] = useState(true)
  const [statusMessage, setStatusMessage] = useState<StatusMessage | null>(null)
  const [refetchCount, setRefetchCount] = useState(0)

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
        setCatalogStatus('loaded')
      } else {
        // Drop the previous catalog: entries left behind would keep rendering stale
        // descriptions and env vars after the user was told the refresh failed.
        setRegisteredInitializers([])
        setCatalogStatus('error')
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
        <ConfiguredInitializers
          items={settings.configured}
          registeredInitializers={registeredInitializers}
          catalogStatus={catalogStatus}
        />
      )}
    </section>
  )
}
