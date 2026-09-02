import { useEffect, useState } from 'react'

import { MessageBar, MessageBarBody, Spinner } from '@fluentui/react-components'

import CustomInitializers from '@/components/Initializers/CustomInitializers'
import { initializersApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { CustomInitializer } from '@/types'

import { useConfigurationStyles } from './Configuration.styles'

interface StatusMessage {
  intent: 'success' | 'error'
  text: string
}

export default function CustomInitializerFiles() {
  const styles = useConfigurationStyles()
  const [items, setItems] = useState<CustomInitializer[]>([])
  const [loading, setLoading] = useState(true)
  const [registering, setRegistering] = useState(false)
  const [deletingName, setDeletingName] = useState<string | null>(null)
  const [statusMessage, setStatusMessage] = useState<StatusMessage | null>(null)

  useEffect(() => {
    let cancelled = false

    const loadAsync = async (): Promise<void> => {
      setLoading(true)
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
  }, [])

  const reload = async (): Promise<void> => {
    const response = await initializersApi.listCustom()
    setItems(response.items)
  }

  const handleRegister = async (name: string, scriptContent: string): Promise<boolean> => {
    setRegistering(true)
    setStatusMessage(null)
    try {
      await initializersApi.register({ name, script_content: scriptContent })
      await reload()
      setStatusMessage({ intent: 'success', text: `Added and registered ${name}.` })
      return true
    } catch (error) {
      setStatusMessage({ intent: 'error', text: toApiError(error).detail })
      return false
    } finally {
      setRegistering(false)
    }
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
