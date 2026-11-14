import { useEffect, useRef, useState } from 'react'

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
  const mountedRef = useRef(false)
  const runtimeEpochRef = useRef(0)
  const catalogRequestVersionRef = useRef(0)
  const [items, setItems] = useState<CustomInitializer[]>([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [registering, setRegistering] = useState(false)
  const [deletingName, setDeletingName] = useState<string | null>(null)
  const [statusMessage, setStatusMessage] = useState<StatusMessage | null>(null)

  const invalidateCatalogReads = (): void => {
    catalogRequestVersionRef.current += 1
  }

  const beginCatalogRead = (): { requestVersion: number; runtimeEpoch: number } => ({
    requestVersion: ++catalogRequestVersionRef.current,
    runtimeEpoch: runtimeEpochRef.current,
  })

  const isCurrentCatalogRead = (requestVersion: number, runtimeEpoch: number): boolean => (
    mountedRef.current
    && requestVersion === catalogRequestVersionRef.current
    && runtimeEpoch === runtimeEpochRef.current
  )

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      runtimeEpochRef.current += 1
      invalidateCatalogReads()
    }
  }, [])

  useEffect(() => {
    const runtimeEpoch = ++runtimeEpochRef.current
    invalidateCatalogReads()
    let cancelled = false

    const loadAsync = async (): Promise<void> => {
      const { requestVersion } = beginCatalogRead()
      try {
        const response = await initializersApi.listCustom()
        if (!cancelled && isCurrentCatalogRead(requestVersion, runtimeEpoch)) {
          setItems(response.items)
        }
      } catch (error) {
        if (!cancelled && isCurrentCatalogRead(requestVersion, runtimeEpoch)) {
          setStatusMessage({ intent: 'error', text: toApiError(error).detail })
        }
      } finally {
        if (!cancelled && isCurrentCatalogRead(requestVersion, runtimeEpoch)) {
          setLoading(false)
        }
      }
    }

    void loadAsync()
    return () => {
      cancelled = true
      if (runtimeEpochRef.current === runtimeEpoch) {
        runtimeEpochRef.current += 1
        invalidateCatalogReads()
      }
    }
  }, [generation])

  const reload = async (): Promise<boolean> => {
    const { requestVersion, runtimeEpoch } = beginCatalogRead()
    try {
      const response = await initializersApi.listCustom()
      if (!isCurrentCatalogRead(requestVersion, runtimeEpoch)) return false
      setItems(response.items)
      return true
    } catch (error) {
      if (!isCurrentCatalogRead(requestVersion, runtimeEpoch)) return false
      throw error
    }
  }

  const handleRetryRefresh = async (): Promise<void> => {
    setRefreshing(true)
    try {
      const applied = await reload()
      if (applied && mountedRef.current) {
        setStatusMessage({ intent: 'success', text: 'Refreshed custom initializers catalog.' })
      }
    } catch (error) {
      if (mountedRef.current) {
        setStatusMessage({
          intent: 'warning',
          text: `Failed to refresh custom initializers: ${toApiError(error).detail}`,
          canRetryRefresh: true,
        })
      }
    } finally {
      if (mountedRef.current) setRefreshing(false)
    }
  }

  const handleRegister = async (name: string, scriptContent: string): Promise<boolean> => {
    const canRetryRefresh = refreshing || statusMessage?.canRetryRefresh === true
    invalidateCatalogReads()
    setRegistering(true)
    setStatusMessage(null)
    try {
      await initializersApi.register({ name, script_content: scriptContent })
    } catch (error) {
      if (mountedRef.current) {
        setStatusMessage({ intent: 'error', text: toApiError(error).detail, canRetryRefresh })
        setRegistering(false)
      }
      return false
    }

    try {
      await reload()
      if (mountedRef.current) {
        setStatusMessage({ intent: 'success', text: `Added and registered ${name}.` })
      }
    } catch (refreshError) {
      if (mountedRef.current) {
        setStatusMessage({
          intent: 'warning',
          text: `Added and registered ${name}, but failed to refresh catalog: ${toApiError(refreshError).detail}`,
          canRetryRefresh: true,
        })
      }
    } finally {
      if (mountedRef.current) setRegistering(false)
    }
    return true
  }

  const handleDelete = async (name: string): Promise<void> => {
    const canRetryRefresh = refreshing || statusMessage?.canRetryRefresh === true
    invalidateCatalogReads()
    setDeletingName(name)
    setStatusMessage(null)
    try {
      await initializersApi.unregister(name)
      await reload()
      if (mountedRef.current) setStatusMessage({ intent: 'success', text: `Removed ${name}.` })
    } catch (error) {
      if (mountedRef.current) setStatusMessage({ intent: 'error', text: toApiError(error).detail, canRetryRefresh })
    } finally {
      if (mountedRef.current) setDeletingName(null)
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
                className={styles.noticeDismiss}
                disabled={refreshing || registering || deletingName !== null}
                onClick={handleRetryRefresh}
              >
                Retry refresh
              </Button>
            )}
            <Button
              appearance="subtle"
              className={styles.noticeDismiss}
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
