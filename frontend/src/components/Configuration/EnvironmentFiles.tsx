import { useEffect, useState } from 'react'

import { Button, Field, MessageBar, MessageBarBody, Spinner } from '@fluentui/react-components'
import { ArrowSyncRegular, SaveRegular } from '@fluentui/react-icons'

import { configurationApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { EnvironmentFileContent } from '@/types'
import EditorWorkspace from '@/components/EditorWorkspace'

import { useConfigurationStyles } from './Configuration.styles'
import DotenvEditor from './DotenvEditor'

interface StatusMessage {
  intent: 'success' | 'error'
  text: string
}

export default function EnvironmentFiles() {
  const styles = useConfigurationStyles()
  const [files, setFiles] = useState<EnvironmentFileContent[]>([])
  const [savedContents, setSavedContents] = useState<Record<string, string>>({})
  const [loadedIds, setLoadedIds] = useState<Set<string>>(() => new Set())
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadingContent, setLoadingContent] = useState(false)
  const [saving, setSaving] = useState(false)
  const [reloadCount, setReloadCount] = useState(0)
  const [statusMessage, setStatusMessage] = useState<StatusMessage | null>(null)

  useEffect(() => {
    let cancelled = false

    const loadFilesAsync = async (): Promise<void> => {
      setLoading(true)
      setStatusMessage(null)
      try {
        const response = await configurationApi.listEnvironmentFiles()
        if (!cancelled) {
          setFiles(response.items)
          setSavedContents({})
          setLoadedIds(new Set())
          setSelectedId((currentId) =>
            response.items.some((file) => file.id === currentId)
              ? currentId
              : response.items[0]?.id ?? null,
          )
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

    void loadFilesAsync()
    return () => {
      cancelled = true
    }
  }, [reloadCount])

  useEffect(() => {
    if (!selectedId || loadedIds.has(selectedId)) return
    let cancelled = false

    const loadContentAsync = async (): Promise<void> => {
      setLoadingContent(true)
      setStatusMessage(null)
      try {
        const loadedFile = await configurationApi.getEnvironmentFile(selectedId)
        if (!cancelled) {
          setFiles((currentFiles) =>
            currentFiles.map((file) => file.id === loadedFile.id ? loadedFile : file),
          )
          setSavedContents((currentContents) => ({ ...currentContents, [loadedFile.id]: loadedFile.content }))
          setLoadedIds((currentIds) => new Set(currentIds).add(loadedFile.id))
        }
      } catch (error) {
        if (!cancelled) {
          setStatusMessage({ intent: 'error', text: toApiError(error).detail })
        }
      } finally {
        if (!cancelled) setLoadingContent(false)
      }
    }

    void loadContentAsync()
    return () => {
      cancelled = true
    }
  }, [loadedIds, selectedId])

  const selectedFile = files.find((file) => file.id === selectedId) ?? null
  const selectedFileIsLoaded = selectedFile ? loadedIds.has(selectedFile.id) : false
  const hasUnsavedChanges = selectedFile && selectedFileIsLoaded
    ? selectedFile.content !== savedContents[selectedFile.id]
    : false

  const handleContentChange = (content: string): void => {
    if (!selectedId) return
    setFiles((currentFiles) =>
      currentFiles.map((file) => file.id === selectedId ? { ...file, content } : file),
    )
  }

  const handleSave = async (): Promise<void> => {
    if (!selectedFile?.version) return
    setSaving(true)
    setStatusMessage(null)
    try {
      const updated = await configurationApi.updateEnvironmentFile(selectedFile.id, {
        content: selectedFile.content,
        version: selectedFile.version,
      })
      setFiles((currentFiles) =>
        currentFiles.map((file) => file.id === updated.id ? updated : file),
      )
      setSavedContents((currentContents) => ({ ...currentContents, [updated.id]: updated.content }))
      setStatusMessage({
        intent: 'success',
        text: `${updated.name} saved. Restart PyRIT to apply these changes.`,
      })
    } catch (error) {
      setStatusMessage({ intent: 'error', text: toApiError(error).detail })
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <div className={styles.loadingState}>
        <Spinner label="Loading environment files..." />
      </div>
    )
  }

  return (
    <div className={styles.environmentSection}>
      {statusMessage && (
        <MessageBar intent={statusMessage.intent} className={styles.message}>
          <MessageBarBody>{statusMessage.text}</MessageBarBody>
        </MessageBar>
      )}
      {selectedFile?.read_only_reason && (
        <MessageBar intent="warning" className={styles.message}>
          <MessageBarBody>{selectedFile.read_only_reason}</MessageBarBody>
        </MessageBar>
      )}

      <EditorWorkspace
        items={files.map((file) => ({
          id: file.id,
          label: file.name,
          secondaryText: file.exists ? file.path : `${file.path} (new)`,
        }))}
        selectedId={selectedId}
        navigationLabel="Environment files"
        emptyMessage="No environment sources are enabled by the configuration."
        description="Edit dotenv sources loaded when PyRIT starts."
        actions={(
          <div className={styles.actions}>
            <Button
              appearance="subtle"
              className={styles.action}
              icon={<ArrowSyncRegular />}
              disabled={saving || loadingContent}
              onClick={() => setReloadCount((count) => count + 1)}
            >
              Reload
            </Button>
            <Button
              appearance="primary"
              className={styles.action}
              icon={<SaveRegular />}
              disabled={saving || loadingContent || selectedFile?.read_only || !hasUnsavedChanges}
              onClick={() => void handleSave()}
            >
              {saving ? 'Saving...' : 'Save'}
            </Button>
          </div>
        )}
        onSelect={setSelectedId}
      >
          {selectedFile && !selectedFileIsLoaded && (
            <div className={styles.loadingState}>
              <Spinner label={`Loading ${selectedFile.name}...`} />
            </div>
          )}
          {selectedFile && selectedFileIsLoaded && (
            <Field
              className={styles.editorField}
              label={selectedFile.path}
              hint={hasUnsavedChanges ? 'Unsaved changes' : 'No unsaved changes'}
            >
              <DotenvEditor
                value={selectedFile.content}
                disabled={saving || selectedFile.read_only === true}
                onChange={handleContentChange}
              />
            </Field>
          )}
      </EditorWorkspace>
    </div>
  )
}
