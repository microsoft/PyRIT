import { useCallback, useEffect, useState } from 'react'

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
import { useBeforeUnload, useBlocker, useSearchParams } from 'react-router'

import { configurationApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import ConfirmDialog from '@/components/ConfirmDialog'
import EditorWorkspace from '@/components/EditorWorkspace'
import Initializers from '@/components/Initializers/Initializers'

import { useConfigurationStyles } from './Configuration.styles'
import CustomInitializerFiles from './CustomInitializerFiles'
import EnvironmentFiles from './EnvironmentFiles'
import YamlEditor from './YamlEditor'

interface StatusMessage {
  intent: 'success' | 'error' | 'warning'
  text: string
}

type ConfigurationTab = 'configuration' | 'environment' | 'initializers' | 'custom-initializers'

function isConfigurationTab(value: unknown): value is ConfigurationTab {
  return value === 'configuration'
    || value === 'environment'
    || value === 'initializers'
    || value === 'custom-initializers'
}

function configurationTabFromSearchParams(searchParams: URLSearchParams): ConfigurationTab {
  const tab = searchParams.get('tab')
  return isConfigurationTab(tab) ? tab : 'configuration'
}

export default function Configuration() {
  const styles = useConfigurationStyles()
  const [searchParams, setSearchParams] = useSearchParams()
  const [content, setContent] = useState('')
  const [savedContent, setSavedContent] = useState('')
  const [source, setSource] = useState('')
  const [version, setVersion] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [reloadCount, setReloadCount] = useState(0)
  const [statusMessage, setStatusMessage] = useState<StatusMessage | null>(null)
  const [environmentHasUnsavedChanges, setEnvironmentHasUnsavedChanges] = useState(false)
  const [pendingDiscardAction, setPendingDiscardAction] = useState<(() => void) | null>(null)
  const selectedTab = configurationTabFromSearchParams(searchParams)
  const configurationHasUnsavedChanges = content !== savedContent
  const hasUnsavedChanges = selectedTab === 'configuration'
    ? configurationHasUnsavedChanges
    : selectedTab === 'environment' && environmentHasUnsavedChanges
  const blocker = useBlocker(hasUnsavedChanges)

  useBeforeUnload(useCallback((event: BeforeUnloadEvent): void => {
    if (hasUnsavedChanges) {
      event.preventDefault()
      event.returnValue = ''
    }
  }, [hasUnsavedChanges]))

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
          setVersion(response.version)
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
    const reload = (): void => {
      setReloadCount((currentCount: number) => currentCount + 1)
    }

    if (configurationHasUnsavedChanges) {
      setPendingDiscardAction(() => reload)
      return
    }
    reload()
  }

  const handleSave = async (): Promise<void> => {
    setSaving(true)
    setStatusMessage(null)
    try {
      const response = await configurationApi.updateContent({ content, version })
      setContent(response.content)
      setSavedContent(response.content)
      setSource(response.source)
      setVersion(response.version)
      setStatusMessage({
        intent: 'success',
        text: 'Configuration saved. Restart PyRIT to apply these changes.',
      })
    } catch (error) {
      setStatusMessage({ intent: 'error', text: toApiError(error).detail })
    } finally {
      setSaving(false)
    }
  }

  const handleTabSelect = (_: SelectTabEvent, data: SelectTabData): void => {
    if (!isConfigurationTab(data.value) || data.value === selectedTab) {
      return
    }

    const nextSearchParams = new URLSearchParams(searchParams)
    if (data.value === 'configuration') {
      nextSearchParams.delete('tab')
    } else {
      nextSearchParams.set('tab', data.value)
    }
    setSearchParams(nextSearchParams)
  }

  const handleDiscardChanges = (): void => {
    const discardAction = pendingDiscardAction
    setPendingDiscardAction(null)
    setContent(savedContent)
    setEnvironmentHasUnsavedChanges(false)
    if (blocker.state === 'blocked') {
      blocker.proceed()
    } else {
      discardAction?.()
    }
  }

  const handleKeepEditing = (): void => {
    setPendingDiscardAction(null)
    if (blocker.state === 'blocked') {
      blocker.reset()
    }
  }

  return (
    <div className={styles.root}>
      <div className={styles.header}>
        <Text as="h1" size={600} weight="semibold">Configuration</Text>
      </div>

      <TabList selectedValue={selectedTab} onTabSelect={handleTabSelect}>
        <Tab value="configuration">PyRIT Configuration</Tab>
        <Tab value="environment">Environment &amp; Secrets</Tab>
        <Tab value="initializers">Initializers</Tab>
        <Tab value="custom-initializers">Custom Initializers</Tab>
      </TabList>

      {selectedTab === 'configuration' && statusMessage && (
        <MessageBar intent={statusMessage.intent} className={styles.message}>
          <MessageBarBody>{statusMessage.text}</MessageBarBody>
        </MessageBar>
      )}

      {selectedTab === 'custom-initializers' ? (
        <CustomInitializerFiles />
      ) : selectedTab === 'initializers' ? (
        <Initializers />
      ) : selectedTab === 'environment' ? (
        <EnvironmentFiles
          onUnsavedChangesChange={setEnvironmentHasUnsavedChanges}
          onRequestDiscardChanges={(discardChanges: () => void): void => {
            setPendingDiscardAction(() => discardChanges)
          }}
        />
      ) : loading ? (
        <div className={styles.loadingState}>
          <Spinner label="Loading PyRIT configuration..." />
        </div>
      ) : (
        <EditorWorkspace
          items={[{ id: 'configuration', label: '.pyrit_conf', secondaryText: source }]}
          selectedId="configuration"
          navigationLabel="Configuration files"
          emptyMessage="Configuration file is unavailable."
          description="Edit YAML configuration loaded when PyRIT starts."
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
                disabled={loading || saving || !configurationHasUnsavedChanges}
                onClick={() => void handleSave()}
              >
                {saving ? 'Saving...' : 'Save'}
              </Button>
            </div>
          )}
        >
          <Field
            className={styles.editorField}
            label={source}
            hint={hasUnsavedChanges ? 'Unsaved changes' : 'Changes take effect after PyRIT restarts.'}
          >
            <YamlEditor
              value={content}
              disabled={saving}
              onChange={setContent}
            />
          </Field>
        </EditorWorkspace>
      )}
      <ConfirmDialog
        open={blocker.state === 'blocked' || pendingDiscardAction !== null}
        title="Discard unsaved changes?"
        confirmLabel="Discard changes"
        cancelLabel="Keep editing"
        onConfirm={handleDiscardChanges}
        onCancel={handleKeepEditing}
      >
        Your unsaved configuration changes will be lost if you continue.
      </ConfirmDialog>
    </div>
  )
}
