import { useState } from 'react'
import { Button, Text, Tooltip } from '@fluentui/react-components'

import type { AdditionalInitializerSetting, RegisteredInitializer, UpdateAdditionalInitializerRequest } from '@/types'

import InitializerParametersDialog from './InitializerParametersDialog'
import { resolveRegisteredInitializer } from './initializerLookup'
import { useInitializerListStyles } from './InitializerList.styles'

interface InitializerListProps {
  items: AdditionalInitializerSetting[]
  registeredInitializers: RegisteredInitializer[]
  savingInitializerId?: string | null
  applyingInitializerId?: string | null
  deletingInitializerId?: string | null
  onSave: (id: string, request: UpdateAdditionalInitializerRequest) => Promise<void>
  onApply: (id: string, initializerName: string, parameters?: Record<string, unknown> | null) => Promise<void>
  onRemove: (id: string) => Promise<void>
}

interface InitializerRowProps {
  item: AdditionalInitializerSetting
  initializer: RegisteredInitializer
  isSaving: boolean
  isApplying: boolean
  isDeleting: boolean
  onSave: (id: string, request: UpdateAdditionalInitializerRequest) => Promise<void>
  onApply: (id: string, initializerName: string, parameters?: Record<string, unknown> | null) => Promise<void>
  onRemove: (id: string) => Promise<void>
}

function serializeParameters(parameters?: Record<string, unknown> | null): string {
  return JSON.stringify(parameters ?? {}, null, 2)
}

function formatSupportedParameterSummary(initializer: RegisteredInitializer): string[] {
  if (initializer.supported_parameters.length === 0) {
    return ['No declared parameters.']
  }

  return initializer.supported_parameters.map((parameter) => {
    const requiredLabel = parameter.required ? 'required' : 'optional'
    return `${parameter.name} (${parameter.type_name}, ${requiredLabel})`
  })
}

function AdditionalInitializerRow({
  item,
  initializer,
  isSaving,
  isApplying,
  isDeleting,
  onSave,
  onApply,
  onRemove,
}: InitializerRowProps) {
  const styles = useInitializerListStyles()
  const [editOpen, setEditOpen] = useState(false)
  const isBusy = isSaving || isApplying || isDeleting

  const handleEditSubmit = async (parameters: Record<string, unknown> | null): Promise<void> => {
    await onSave(item.id, { parameters, order_index: item.order_index ?? null })
    setEditOpen(false)
  }

  return (
    <div
      role="listitem"
      className={styles.card}
      data-testid={`initializer-row-${item.id}`}
    >
      <div className={styles.cardHeader}>
        <div className={styles.titleGroup}>
          <Tooltip
            content={initializer.description || 'No description available.'}
            relationship="description"
            withArrow
          >
            <Text weight="semibold" size={400}>
              {item.initializer_name}
            </Text>
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
        <pre className={styles.parametersBlock}>{serializeParameters(item.parameters)}</pre>
      </div>

      <div className={styles.actionsRow}>
        <Button
          appearance="primary"
          onClick={() => setEditOpen(true)}
          disabled={isBusy}
        >
          Edit
        </Button>
        <Button
          appearance="secondary"
          onClick={() => void onApply(item.id, item.initializer_name, item.parameters)}
          disabled={isBusy}
        >
          {isApplying ? 'Applying...' : 'Apply now'}
        </Button>
        <Button
          appearance="subtle"
          onClick={() => void onRemove(item.id)}
          disabled={isBusy}
        >
          {isDeleting ? 'Removing...' : 'Remove'}
        </Button>
      </div>

      <InitializerParametersDialog
        open={editOpen}
        mode="edit"
        initializer={initializer}
        initialParameters={item.parameters}
        submitting={isSaving}
        onSubmit={handleEditSubmit}
        onOpenChange={setEditOpen}
      />
    </div>
  )
}

export default function InitializerList({
  items,
  registeredInitializers,
  savingInitializerId = null,
  applyingInitializerId = null,
  deletingInitializerId = null,
  onSave,
  onApply,
  onRemove,
}: InitializerListProps) {
  const styles = useInitializerListStyles()

  return (
    <div className={styles.list} role="list" aria-label="Additional initializers">
      {items.map((item: AdditionalInitializerSetting) => (
        <AdditionalInitializerRow
          key={`${item.id}:${serializeParameters(item.parameters)}:${item.order_index ?? ''}`}
          item={item}
          initializer={resolveRegisteredInitializer(item.initializer_name, registeredInitializers)}
          isSaving={savingInitializerId === item.id}
          isApplying={applyingInitializerId === item.id}
          isDeleting={deletingInitializerId === item.id}
          onSave={onSave}
          onApply={onApply}
          onRemove={onRemove}
        />
      ))}
    </div>
  )
}
