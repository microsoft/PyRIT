import { useEffect, useState } from 'react'

import { Button, MessageBar, MessageBarActions, MessageBarBody, Spinner } from '@fluentui/react-components'
import { DismissRegular } from '@fluentui/react-icons'

import CustomInitializers from '@/components/Initializers/CustomInitializers'
import { initializersApi } from '@/services/api'
import { useRuntime } from '@/hooks/useRuntime'
import { toApiError } from '@/services/errors'
import type { CustomInitializer } from '@/types'

import { useConfigurationStyles } from './Configuration.styles'

interface StatusMessage {
  intent: 'success' | 'error' | 'warning'
  text: string
  canRetryRefresh?: boolean
}

export default function CustomInitializerFiles() {
  const { generation } = useRuntime()
  const styles = useConfigurationStyles()
  const [items, setItems] = useState<CustomInitializer[]>([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [registering, setRegistering] = useState(false)
  const [deletingName, setDeletingName] = useState<string | null>(null)
  const [statusMessage, setStatusMessage] = useState<StatusMessage | null>(null)

  useEffect(() => {
    let cancelled = false

    const loadAsync = async (): Promise<void> => {
      try {
        const response = await initializersApi.listCustom()
        if (!cancelled) {
          setItems(response.items)
        }
      } catch (error) {
        if (!cancelled) {
          setStatusMessage({ intent: 'error', text: toApiError(error).detail })
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    void loadAsync()
    return () => {
      cancelled = true
    }
  }, [generation])

  const reload = async (): Promise<void> => {
    const response = await initializersApi.listCustom()
    setItems(response.items)
  }

  const handleRetryRefresh = async (): Promise<void> => {
    setRefreshing(true)
    try {
      await reload()
      setStatusMessage({ intent: 'success', text: 'Refreshed custom initializers catalog.' })
    } catch (error) {
      setStatusMessage({
        intent: 'warning',
        text: `Failed to refresh custom initializers: ${toApiError(error).detail}`,
        canRetryRefresh: true,
      })
    } finally {
      setRefreshing(false)
    }
  }

  const handleRegister = async (name: string, scriptContent: string): Promise<boolean> => {
    setRegistering(true)
    setStatusMessage(null)
    try {
      await initializersApi.register({ name, script_content: scriptContent })
    } catch (error) {
      setStatusMessage({ intent: 'error', text: toApiError(error).detail })
      setRegistering(false)
      return false
    }

    try {
      await reload()
      setStatusMessage({ intent: 'success', text: `Added and registered ${name}.` })
    } catch (refreshError) {
      setStatusMessage({
        intent: 'warning',
        text: `Added and registered ${name}, but failed to refresh catalog: ${toApiError(refreshError).detail}`,
        canRetryRefresh: true,
      })
    } finally {
      setRegistering(false)
    }
    return true
  }

  const handleDelete = async (name: string): Promise<void> => {
    setDeletingName(name)
    setStatusMessage(null)
    try {
      await initializersApi.unregister(name)
      await reload()
      setStatusMessage({ intent: 'success', text: `Removed ${name}.` })
    } catch (error) {
      setStatusMessage({ intent: 'error', text: toApiError(error).detail })
    } finally {
      setDeletingName(null)
    }
  }

  if (loading) {
    return <Spinner label="Loading custom initializers..." />
  }

  return (
    <div className={styles.environmentSection}>
      {statusMessage && (
        <MessageBar intent={statusMessage.intent}>
          <MessageBarBody>{statusMessage.text}</MessageBarBody>
          <MessageBarActions>
            {statusMessage.canRetryRefresh && (
              <Button
                appearance="secondary"
                disabled={refreshing || registering || deletingName !== null}
                onClick={handleRetryRefresh}
              >
                Retry refresh
              </Button>
            )}
            <Button
              appearance="subtle"
              icon={<DismissRegular />}
              aria-label="Dismiss notification"
              onClick={() => setStatusMessage(null)}
            />
          </MessageBarActions>
        </MessageBar>
      )}
      <CustomInitializers
        items={items}
        registering={registering}
        deletingName={deletingName}
        onRegister={handleRegister}
        onDelete={handleDelete}
      />
    </div>
  )
}
