import { useEffect, useState } from 'react'

import {
  Button,
  Field,
  MessageBar,
  MessageBarBody,
  Spinner,
  Tab,
  TabList,
  Text,
} from '@fluentui/react-components'
import type { SelectTabData, SelectTabEvent } from '@fluentui/react-components'
import { ArrowSyncRegular, SaveRegular } from '@fluentui/react-icons'

import { configurationApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import EditorWorkspace from '@/components/EditorWorkspace'

import { useBackendConfigurationStyles } from './BackendConfiguration.styles'
import CustomInitializerFiles from './CustomInitializerFiles'
import EnvironmentFiles from './EnvironmentFiles'
import YamlEditor from './YamlEditor'

interface StatusMessage {
  intent: 'success' | 'error' | 'warning'
  text: string
}

type ConfigurationTab = 'configuration' | 'environment' | 'custom-initializers'

export default function BackendConfiguration() {
  const styles = useBackendConfigurationStyles()
  const [content, setContent] = useState('')
  const [savedContent, setSavedContent] = useState('')
  const [source, setSource] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [restarting, setRestarting] = useState(false)
  const [reloadCount, setReloadCount] = useState(0)
  const [statusMessage, setStatusMessage] = useState<StatusMessage | null>(null)
  const [selectedTab, setSelectedTab] = useState<ConfigurationTab>('configuration')

  useEffect(() => {
    let cancelled = false

    const loadContentAsync = async (): Promise<void> => {
      setLoading(true)
      setStatusMessage(null)
      try {
        const response = await configurationApi.getContent()
        if (!cancelled) {
          setContent(response.content)
          setSavedContent(response.content)
          setSource(response.source)
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

    void loadContentAsync()
    return () => {
      cancelled = true
    }
  }, [reloadCount])

  const handleReload = (): void => {
    setReloadCount((currentCount: number) => currentCount + 1)
  }

  const handleSave = async (): Promise<void> => {
    setSaving(true)
    setStatusMessage(null)
    try {
      const response = await configurationApi.updateContent({ content })
      setContent(response.content)
      setSavedContent(response.content)
      setSource(response.source)
      setStatusMessage({
        intent: 'success',
        text: 'Configuration saved. Restart the backend to apply these changes.',
      })
    } catch (error) {
      setStatusMessage({ intent: 'error', text: toApiError(error).detail })
    } finally {
      setSaving(false)
    }
  }

  const handleRestart = async (): Promise<void> => {
    setRestarting(true)
    setStatusMessage(null)
    try {
      await configurationApi.restart()
      setStatusMessage({ intent: 'success', text: 'Backend restart requested.' })
    } catch (error) {
      setStatusMessage({ intent: 'error', text: toApiError(error).detail })
    } finally {
      setRestarting(false)
    }
  }

  const hasUnsavedChanges = content !== savedContent

  const handleTabSelect = (_: SelectTabEvent, data: SelectTabData): void => {
    if (
      data.value === 'configuration'
      || data.value === 'environment'
      || data.value === 'custom-initializers'
    ) {
      setSelectedTab(data.value)
    }
  }

  return (
    <main className={styles.root}>
      <div className={styles.header}>
        <Text as="h1" size={600} weight="semibold">Backend Configuration</Text>
        <Button
          appearance="secondary"
          icon={<ArrowSyncRegular />}
          disabled={restarting || saving}
          onClick={() => void handleRestart()}
        >
          {restarting ? 'Restarting...' : 'Restart backend'}
        </Button>
      </div>

      <TabList selectedValue={selectedTab} onTabSelect={handleTabSelect}>
        <Tab value="configuration">Configuration</Tab>
        <Tab value="environment">Environment Files</Tab>
        <Tab value="custom-initializers">Custom Initializers</Tab>
      </TabList>

      {selectedTab === 'configuration' && statusMessage && (
        <MessageBar intent={statusMessage.intent} className={styles.message}>
          <MessageBarBody>{statusMessage.text}</MessageBarBody>
        </MessageBar>
      )}

      {selectedTab === 'custom-initializers' ? (
        <CustomInitializerFiles />
      ) : selectedTab === 'environment' ? (
        <EnvironmentFiles />
      ) : loading ? (
        <div className={styles.loadingState}>
          <Spinner label="Loading backend configuration..." />
        </div>
      ) : (
        <EditorWorkspace
          items={[{ id: 'configuration', label: '.pyrit_conf', secondaryText: source }]}
          selectedId="configuration"
          navigationLabel="Configuration files"
          emptyMessage="Configuration file is unavailable."
          description="Edit YAML configuration loaded when the backend starts."
          actions={(
            <div className={styles.actions}>
              <Button
                appearance="subtle"
                className={styles.action}
                icon={<ArrowSyncRegular />}
                disabled={loading || saving}
                onClick={handleReload}
              >
                Reload
              </Button>
              <Button
                appearance="primary"
                className={styles.action}
                icon={<SaveRegular />}
                disabled={loading || saving || !hasUnsavedChanges}
                onClick={() => void handleSave()}
              >
                {saving ? 'Saving...' : 'Save'}
              </Button>
            </div>
          )}
        >
          <Field
            className={styles.editorField}
            label=".pyrit_conf"
            hint={hasUnsavedChanges ? 'Unsaved changes' : 'Changes take effect after the backend restarts.'}
          >
            <YamlEditor
              value={content}
              disabled={saving}
              onChange={setContent}
            />
          </Field>
        </EditorWorkspace>
      )}
    </main>
  )
}