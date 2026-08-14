import { useState } from 'react'

import {
  Button,
  Select,
  Text,
  Tooltip,
} from '@fluentui/react-components'
import { AddRegular } from '@fluentui/react-icons'

import type {
  AdditionalInitializerSetting,
  RegisteredInitializer,
  UpdateAdditionalInitializerRequest,
} from '@/types'

import { toApiError } from '@/services/errors'
import { useAdditionalInitializersStyles } from './AdditionalInitializers.styles'
import { formatInitializerParameters, formatSupportedParameterSummary } from './initializerFormatting'
import { resolveRegisteredInitializer } from './initializerLookup'
import InitializerParametersDialog from './InitializerParametersDialog'
import { useInitializersStyles } from './Initializers.styles'
import ConfirmDialog from '../ConfirmDialog'

interface AdditionalInitializersProps {
  items: AdditionalInitializerSetting[]
  registeredInitializers: RegisteredInitializer[]
  creating: boolean
  savingInitializerId?: string | null
  saveErrors?: Record<string, string>
  applyingInitializerId?: string | null
  deletingInitializerId?: string | null
  onAdd: (initializerName: string, parameters: Record<string, unknown> | null) => Promise<boolean>
  onSave: (id: string, request: UpdateAdditionalInitializerRequest) => Promise<boolean>
  onClearSaveError: (id: string) => void
  onApply: (id: string, initializerName: string, parameters?: Record<string, unknown> | null) => Promise<void>
  onRemove: (id: string) => Promise<void>
}

interface AdditionalInitializerCardProps {
  item: AdditionalInitializerSetting
  initializer: RegisteredInitializer
  isSaving: boolean
  isApplying: boolean
  isDeleting: boolean
  saveError?: string | null
  onSave: (id: string, request: UpdateAdditionalInitializerRequest) => Promise<boolean>
  onClearSaveError: (id: string) => void
  onApply: (id: string, initializerName: string, parameters?: Record<string, unknown> | null) => Promise<void>
  onRemove: (id: string) => Promise<void>
}

function AdditionalInitializerCard({
  item,
  initializer,
  isSaving,
  isApplying,
  isDeleting,
  saveError,
  onSave,
  onClearSaveError,
  onApply,
  onRemove,
}: AdditionalInitializerCardProps) {
  const styles = useAdditionalInitializersStyles()
  const [editOpen, setEditOpen] = useState(false)
  const [confirmRemoveOpen, setConfirmRemoveOpen] = useState(false)
  const isBusy = isSaving || isApplying || isDeleting

  const handleEditSubmit = async (parameters: Record<string, unknown> | null): Promise<void> => {
    const saved = await onSave(item.id, { parameters, order_index: item.order_index ?? null })
    if (saved) {
      setEditOpen(false)
    }
  }

  const handleEditOpenChange = (open: boolean): void => {
    setEditOpen(open)
    if (!open) {
      onClearSaveError(item.id)
    }
  }

  return (
    <div role="listitem" className={styles.card} data-testid={`initializer-row-${item.id}`}>
      <div className={styles.cardHeader}>
        <div className={styles.titleGroup}>
          <Tooltip content={initializer.description || 'No description available.'} relationship="description" withArrow>
            <Text weight="semibold" size={400}>{item.initializer_name}</Text>
          </Tooltip>
          {initializer.required_env_vars.length > 0 && (
            <Text className={styles.envVarText}>
              Required env vars: {initializer.required_env_vars.join(', ')}
            </Text>
          )}
        </div>
      </div>

      <div className={styles.parameterList}>
        {formatSupportedParameterSummary(initializer).map((summary: string) => (
          <Text key={summary} className={styles.parameterHint} size={200}>
            {summary}
          </Text>
        ))}
      </div>

      <div>
        <Text weight="semibold" size={300}>Parameters</Text>
        <pre className={styles.parametersBlock}>{formatInitializerParameters(item.parameters)}</pre>
      </div>

      <div className={styles.actionsRow}>
        <Button appearance="primary" onClick={() => setEditOpen(true)} disabled={isBusy}>
          Edit
        </Button>
        <Button
          appearance="secondary"
          onClick={() => void onApply(item.id, item.initializer_name, item.parameters)}
          disabled={isBusy}
        >
          {isApplying ? 'Applying...' : 'Apply now'}
        </Button>
        <Button appearance="subtle" onClick={() => setConfirmRemoveOpen(true)} disabled={isBusy}>
          {isDeleting ? 'Removing...' : 'Remove'}
        </Button>
      </div>

      <ConfirmDialog
        open={confirmRemoveOpen}
        title="Remove initializer"
        confirmLabel="Remove"
        onConfirm={() => {
          setConfirmRemoveOpen(false)
          void onRemove(item.id)
        }}
        onCancel={() => setConfirmRemoveOpen(false)}
      >
        Are you sure you want to remove the <Text weight="semibold">{item.initializer_name}</Text> initializer? This action cannot be undone.
      </ConfirmDialog>

      {editOpen && (
        <InitializerParametersDialog
          open
          mode="edit"
          initializer={initializer}
          initialParameters={item.parameters}
          submitting={isSaving}
          externalError={saveError}
          onSubmit={handleEditSubmit}
          onOpenChange={handleEditOpenChange}
        />
      )}
    </div>
  )
}

export default function AdditionalInitializers({
  items,
  registeredInitializers,
  creating,
  savingInitializerId = null,
  saveErrors = {},
  applyingInitializerId = null,
  deletingInitializerId = null,
  onAdd,
  onSave,
  onClearSaveError,
  onApply,
  onRemove,
}: AdditionalInitializersProps) {
  const pageStyles = useInitializersStyles()
  const listStyles = useAdditionalInitializersStyles()
  const [selectedInitializerName, setSelectedInitializerName] = useState('')
  const [addDialogOpen, setAddDialogOpen] = useState(false)
  const [addError, setAddError] = useState<string | null>(null)
  const initializerName = selectedInitializerName || registeredInitializers[0]?.initializer_name || ''
  const selectedInitializer = registeredInitializers.find(
    (initializer) => initializer.initializer_name === initializerName,
  ) ?? null

  const handleAdd = async (parameters: Record<string, unknown> | null): Promise<void> => {
    if (!initializerName) {
      return
    }
    setAddError(null)
    try {
      const added = await onAdd(initializerName, parameters)
      if (added) {
        setAddDialogOpen(false)
      }
    } catch (e) {
      setAddError(toApiError(e).detail)
    }
  }

  return (
    <section className={pageStyles.section} aria-labelledby="additional-initializers-heading">
      <div className={pageStyles.sectionHeader}>
        <Text as="h2" id="additional-initializers-heading" size={500} weight="semibold">
          Additional initializers
        </Text>
        <Text size={300} className={pageStyles.metadataText}>
          Add and edit initializer invocations that run after the baseline.
        </Text>
      </div>

      <div className={pageStyles.addInitializerRow}>
        <Select
          aria-label="Initializer to add"
          className={pageStyles.addInitializerSelect}
          value={initializerName}
          disabled={creating || registeredInitializers.length === 0}
          onChange={(_event, data) => setSelectedInitializerName(data.value)}
        >
          {registeredInitializers.map((initializer: RegisteredInitializer) => (
            <option key={initializer.initializer_name} value={initializer.initializer_name}>
              {initializer.initializer_name}
            </option>
          ))}
        </Select>
        <Button
          appearance="primary"
          icon={<AddRegular />}
          className={pageStyles.touchTarget}
          onClick={() => setAddDialogOpen(true)}
          disabled={creating || !initializerName}
        >
          {creating ? 'Adding...' : 'Add initializer'}
        </Button>
      </div>

      {items.length === 0 ? (
        <Text className={pageStyles.emptyState}>No additional initializers are configured.</Text>
      ) : (
        <div className={listStyles.list} role="list" aria-label="Additional initializers">
          {items.map((item: AdditionalInitializerSetting) => (
            <AdditionalInitializerCard
              key={`${item.id}:${formatInitializerParameters(item.parameters)}:${item.order_index ?? ''}`}
              item={item}
              initializer={resolveRegisteredInitializer(item.initializer_name, registeredInitializers)}
              isSaving={savingInitializerId === item.id}
              isApplying={applyingInitializerId === item.id}
              isDeleting={deletingInitializerId === item.id}
              saveError={saveErrors[item.id] ?? null}
              onSave={onSave}
              onClearSaveError={onClearSaveError}
              onApply={onApply}
              onRemove={onRemove}
            />
          ))}
        </div>
      )}

      {addDialogOpen && (
        <InitializerParametersDialog
          key={selectedInitializer?.initializer_name ?? 'none'}
          open
          mode="add"
          initializer={selectedInitializer}
          initialParameters={null}
          submitting={creating}
          externalError={addError}
          onSubmit={handleAdd}
          onOpenChange={(open) => {
            setAddDialogOpen(open)
            if (!open) {
              setAddError(null)
            }
          }}
        />
      )}
    </section>
  )
}
