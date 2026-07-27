import { useEffect, useState } from 'react'
import {
  Button,
  MessageBar,
  MessageBarBody,
  Spinner,
  Text,
} from '@fluentui/react-components'
import { ArrowSyncRegular } from '@fluentui/react-icons'

import { initializersApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type {
  EffectiveInitializerSetting,
  UpdateInitializerSettingRequest,
} from '@/types'

import InitializerList from './InitializerList'
import { useInitializerConfigStyles } from './InitializerConfig.styles'

interface StatusMessage {
  intent: 'success' | 'error'
  text: string
}

export default function InitializerConfig() {
  const styles = useInitializerConfigStyles()
  const [items, setItems] = useState<EffectiveInitializerSetting[]>([])
  const [loading, setLoading] = useState(true)
  const [statusMessage, setStatusMessage] = useState<StatusMessage | null>(null)
  const [refetchCount, setRefetchCount] = useState(0)
  const [savingInitializerName, setSavingInitializerName] = useState<string | null>(null)
  const [applyingInitializerName, setApplyingInitializerName] = useState<string | null>(null)
  const [resettingInitializerName, setResettingInitializerName] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    const loadSettingsAsync = async (): Promise<void> => {
      try {
        const response = await initializersApi.getSettings()
        if (cancelled) {
          return
        }
        setItems(response.items)
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

    void loadSettingsAsync()
    return () => {
      cancelled = true
    }
  }, [refetchCount])

  const refreshSettings = (clearStatusMessage: boolean = true): void => {
    setLoading(true)
    if (clearStatusMessage) {
      setStatusMessage(null)
    }
    setRefetchCount((currentCount) => currentCount + 1)
  }

  const handleSave = async (
    initializerName: string,
    request: UpdateInitializerSettingRequest,
  ): Promise<void> => {
    setSavingInitializerName(initializerName)

    try {
      await initializersApi.updateSettings(initializerName, request)
      setStatusMessage({ intent: 'success', text: `Saved settings for ${initializerName}.` })
      refreshSettings(false)
    } catch (error) {
      setStatusMessage({ intent: 'error', text: toApiError(error).detail })
    } finally {
      setSavingInitializerName(null)
    }
  }

  const handleApply = async (
    initializerName: string,
    parameters?: Record<string, unknown> | null,
  ): Promise<void> => {
    setApplyingInitializerName(initializerName)

    try {
      // An explicit {} (rather than null/undefined) tells the backend "apply with no
      // parameters," distinct from omitting parameters entirely, which would fall back
      // to any saved override parameters instead.
      await initializersApi.applyNow(initializerName, { parameters: parameters ?? {} })
      setStatusMessage({ intent: 'success', text: `Applied ${initializerName}.` })
    } catch (error) {
      setStatusMessage({ intent: 'error', text: toApiError(error).detail })
    } finally {
      setApplyingInitializerName(null)
    }
  }

  const handleReset = async (initializerName: string): Promise<void> => {
    setResettingInitializerName(initializerName)

    try {
      await initializersApi.clearSettings(initializerName)
      setStatusMessage({ intent: 'success', text: `Cleared saved settings for ${initializerName}.` })
      refreshSettings(false)
    } catch (error) {
      setStatusMessage({ intent: 'error', text: toApiError(error).detail })
    } finally {
      setResettingInitializerName(null)
    }
  }

  return (
    <section className={styles.root} data-testid="initializer-config">
      <div className={styles.header}>
        <div className={styles.headerText}>
          <Text size={300}>
            Configure how targets are auto-registered from your environment, save your changes to the database, or apply them now.
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
      ) : items.length === 0 ? (
        <Text className={styles.emptyState}>No target auto-registration is configured.</Text>
      ) : (
        <InitializerList
          items={items}
          savingInitializerName={savingInitializerName}
          applyingInitializerName={applyingInitializerName}
          resettingInitializerName={resettingInitializerName}
          onSave={handleSave}
          onApply={handleApply}
          onReset={handleReset}
        />
      )}
    </section>
  )
}

