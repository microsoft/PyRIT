import { useState } from 'react'
import { Button, Field, Input, Text, Textarea, Tooltip } from '@fluentui/react-components'

import type { AdditionalInitializerSetting, RegisteredInitializer, UpdateAdditionalInitializerRequest } from '@/types'

import { useInitializerListStyles } from './InitializerList.styles'

interface InitializerListProps {
  items: AdditionalInitializerSetting[]
  savingInitializerId?: string | null
  applyingInitializerId?: string | null
  deletingInitializerId?: string | null
  onSave: (id: string, request: UpdateAdditionalInitializerRequest) => Promise<void>
  onApply: (id: string, initializerName: string, parameters?: Record<string, unknown> | null) => Promise<void>
  onRemove: (id: string) => Promise<void>
}

interface InitializerRowProps {
  item: AdditionalInitializerSetting
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

function parseParametersText(text: string): Record<string, unknown> | null {
  const trimmed = text.trim()
  if (!trimmed) {
    return null
  }

  const parsed: unknown = JSON.parse(trimmed)
  if (parsed === null) {
    return null
  }
  if (typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('Parameters must be a JSON object.')
  }
  return parsed as Record<string, unknown>
}

function parseOrderIndex(value: string): number | null {
  const trimmed = value.trim()
  if (!trimmed) {
    return null
  }

  const parsed = Number(trimmed)
  if (!Number.isInteger(parsed)) {
    throw new Error('Order must be a whole number.')
  }
  return parsed
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
  isSaving,
  isApplying,
  isDeleting,
  onSave,
  onApply,
  onRemove,
}: InitializerRowProps) {
  const styles = useInitializerListStyles()
  const [parametersText, setParametersText] = useState(() => serializeParameters(item.parameters))
  const [orderIndexText, setOrderIndexText] = useState(() => item.order_index?.toString() ?? '')
  const [error, setError] = useState<string | null>(null)
  const isBusy = isSaving || isApplying || isDeleting

  const parseDraft = (): UpdateAdditionalInitializerRequest | null => {
    try {
      return {
        parameters: parseParametersText(parametersText),
        order_index: parseOrderIndex(orderIndexText),
      }
    } catch (parseError) {
      const message = parseError instanceof Error ? parseError.message : 'Invalid initializer settings.'
      setError(message)
      return null
    }
  }

  const handleSave = async (): Promise<void> => {
    const parsedDraft = parseDraft()
    if (!parsedDraft) {
      return
    }

    setError(null)
    await onSave(item.id, parsedDraft)
  }

  const handleApply = async (): Promise<void> => {
    const parsedDraft = parseDraft()
    if (!parsedDraft) {
      return
    }

    setError(null)
    await onApply(item.id, item.initializer.initializer_name, parsedDraft.parameters)
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
            content={item.initializer.description || 'No description available.'}
            relationship="description"
            withArrow
          >
            <Text weight="semibold" size={400}>
              {item.initializer.initializer_name}
            </Text>
          </Tooltip>
          <Text className={styles.parameterHint} size={200}>
            Order: {item.order_index ?? 'not set'}
          </Text>
          {item.initializer.required_env_vars.length > 0 && (
            <Text className={styles.envVarText}>
              Required env vars: {item.initializer.required_env_vars.join(', ')}
            </Text>
          )}
        </div>
      </div>

      <div className={styles.parameterList}>
        {formatSupportedParameterSummary(item.initializer).map((summary: string) => (
          <Text key={summary} className={styles.parameterHint} size={200}>
            {summary}
          </Text>
        ))}
      </div>

      <Field label="Parameters JSON">
        <Textarea
          className={styles.parametersEditor}
          value={parametersText}
          onChange={(_, data) => {
            setParametersText(data.value)
            setError(null)
          }}
          disabled={isBusy}
        />
      </Field>
      <Field label="Order index">
        <Input
          className={styles.orderInput}
          type="number"
          value={orderIndexText}
          onChange={(_, data) => {
            setOrderIndexText(data.value)
            setError(null)
          }}
          disabled={isBusy}
        />
      </Field>
      {error && (
        <Text role="alert" className={styles.errorText}>
          {error}
        </Text>
      )}

      <div className={styles.actionsRow}>
        <Button
          appearance="primary"
          onClick={() => void handleSave()}
          disabled={isBusy}
        >
          {isSaving ? 'Saving...' : 'Save'}
        </Button>
        <Button
          appearance="secondary"
          onClick={() => void handleApply()}
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
    </div>
  )
}

export default function InitializerList({
  items,
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
