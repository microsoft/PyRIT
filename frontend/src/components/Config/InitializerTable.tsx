import { useState } from 'react'
import {
  Badge,
  Button,
  Field,
  Table,
  TableBody,
  TableCell,
  TableHeader,
  TableHeaderCell,
  TableRow,
  Text,
  Textarea,
  Tooltip,
} from '@fluentui/react-components'

import type {
  EffectiveInitializerSetting,
  UpdateInitializerSettingRequest,
} from '@/types'

import { useInitializerTableStyles } from './InitializerTable.styles'

interface RowDraft {
  enabled: boolean
  savedOrderIndex: number | null
  parametersText: string
  initialParametersText: string
  error: string | null
}

interface InitializerTableProps {
  items: EffectiveInitializerSetting[]
  savingInitializerName?: string | null
  applyingInitializerName?: string | null
  resettingInitializerName?: string | null
  onSave: (initializerName: string, request: UpdateInitializerSettingRequest) => Promise<void>
  onApply: (initializerName: string, parameters?: Record<string, unknown> | null) => Promise<void>
  onReset: (initializerName: string) => Promise<void>
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

function formatSupportedParameterSummary(initializer: EffectiveInitializerSetting): string[] {
  if (initializer.supported_parameters.length === 0) {
    return ['No declared parameters.']
  }

  return initializer.supported_parameters.map((parameter) => {
    const requiredLabel = parameter.required ? 'required' : 'optional'
    return `${parameter.name} (${parameter.type_name}, ${requiredLabel})`
  })
}

const SOURCE_DETAILS: Record<
  EffectiveInitializerSetting['source'],
  { label: string; tooltip: string }
> = {
  baseline: {
    label: 'From config file',
    tooltip:
      "This initializer comes from your deployment's config file (.pyrit_conf) and has not been changed here.",
  },
  override: {
    label: 'Added in GUI',
    tooltip:
      "This initializer is not in your deployment's config file. It was added and is saved only here in the GUI.",
  },
  'baseline+override': {
    label: 'Customized',
    tooltip:
      "This initializer is defined in your deployment's config file, but its settings have been changed and saved here.",
  },
}

export default function InitializerTable({
  items,
  savingInitializerName = null,
  applyingInitializerName = null,
  resettingInitializerName = null,
  onSave,
  onApply,
  onReset,
}: InitializerTableProps) {
  const styles = useInitializerTableStyles()
  const [seenItems, setSeenItems] = useState<EffectiveInitializerSetting[] | null>(null)
  const [drafts, setDrafts] = useState<Record<string, RowDraft>>({})

  if (items !== seenItems) {
    const nextDrafts = items.reduce<Record<string, RowDraft>>((accumulator, item) => {
      const initialParametersText = serializeParameters(item.parameters)
      accumulator[item.initializer_name] = {
        enabled: item.enabled,
        savedOrderIndex: item.saved_order_index ?? null,
        parametersText: initialParametersText,
        initialParametersText,
        error: null,
      }
      return accumulator
    }, {})
    setSeenItems(items)
    setDrafts(nextDrafts)
  }

  const updateDraft = (initializerName: string, patch: Partial<RowDraft>): void => {
    setDrafts((currentDrafts) => ({
      ...currentDrafts,
      [initializerName]: {
        ...currentDrafts[initializerName],
        ...patch,
      },
    }))
  }

  const parseDraft = (initializerName: string): UpdateInitializerSettingRequest | null => {
    const draft = drafts[initializerName]
    if (!draft) {
      return null
    }

    try {
      return {
        enabled: draft.enabled,
        parameters: parseParametersText(draft.parametersText),
        order_index: draft.savedOrderIndex,
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Invalid initializer settings.'
      updateDraft(initializerName, { error: message })
      return null
    }
  }

  const handleSave = async (initializerName: string): Promise<void> => {
    const parsedDraft = parseDraft(initializerName)
    if (!parsedDraft) {
      return
    }

    updateDraft(initializerName, { error: null })
    await onSave(initializerName, parsedDraft)
  }

  const handleApply = async (initializerName: string): Promise<void> => {
    const parsedDraft = parseDraft(initializerName)
    if (!parsedDraft) {
      return
    }

    updateDraft(initializerName, { error: null })
    await onApply(initializerName, parsedDraft.parameters)
  }

  const handleReset = async (initializerName: string): Promise<void> => {
    updateDraft(initializerName, { error: null })
    await onReset(initializerName)
  }

  return (
    <div className={styles.tableContainer}>
      <Table aria-label="Initializer settings" className={styles.table}>
        <TableHeader>
          <TableRow>
            <TableHeaderCell>Name</TableHeaderCell>
            <TableHeaderCell>
              <Tooltip
                content="Where this initializer's settings come from: your deployment's config file, a setting added here in the GUI, or a config-file initializer customized here."
                relationship="description"
                withArrow
              >
                <span>Source</span>
              </Tooltip>
            </TableHeaderCell>
            <TableHeaderCell>Parameters</TableHeaderCell>
            <TableHeaderCell>Actions</TableHeaderCell>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((item) => {
            const draft = drafts[item.initializer_name]
            if (!draft) {
              return null
            }

            const isSaving = savingInitializerName === item.initializer_name
            const isApplying = applyingInitializerName === item.initializer_name
            const isResetting = resettingInitializerName === item.initializer_name
            const isBusy = isSaving || isApplying || isResetting
            const isUnchanged = draft.parametersText === draft.initialParametersText

            return (
              <TableRow
                key={item.initializer_name}
                data-testid={`initializer-row-${item.initializer_name}`}
              >
                <TableCell className={styles.cell}>
                  <Tooltip
                    content={item.description || 'No description available.'}
                    relationship="description"
                    withArrow
                  >
                    <Text weight="semibold">{item.initializer_name}</Text>
                  </Tooltip>
                  {item.required_env_vars.length > 0 && (
                    <Text className={styles.envVarText}>
                      Required env vars: {item.required_env_vars.join(', ')}
                    </Text>
                  )}
                </TableCell>
                <TableCell className={styles.cell}>
                  <Tooltip content={SOURCE_DETAILS[item.source].tooltip} relationship="description" withArrow>
                    <Badge appearance="outline" className={styles.sourceBadge}>
                      {SOURCE_DETAILS[item.source].label}
                    </Badge>
                  </Tooltip>
                </TableCell>
                <TableCell className={styles.parametersCell}>
                  <div className={styles.parameterList}>
                    {formatSupportedParameterSummary(item).map((summary) => (
                      <Text key={summary} className={styles.parameterHint} size={200}>
                        {summary}
                      </Text>
                    ))}
                  </div>
                  <Field label="Parameters JSON">
                    <Textarea
                      className={styles.parametersEditor}
                      value={draft.parametersText}
                      onChange={(_, data) =>
                        updateDraft(item.initializer_name, { parametersText: data.value, error: null })
                      }
                      disabled={isBusy}
                    />
                  </Field>
                  {draft.error && (
                    <Text role="alert" className={styles.errorText}>
                      {draft.error}
                    </Text>
                  )}
                </TableCell>
                <TableCell className={styles.actionsCell}>
                  <div className={styles.actionsColumn}>
                    {isUnchanged && !isBusy ? (
                      <Tooltip content="No changes to save." relationship="description" withArrow>
                        <Button appearance="primary" disabled>
                          Save
                        </Button>
                      </Tooltip>
                    ) : (
                      <Button
                        appearance="primary"
                        onClick={() => void handleSave(item.initializer_name)}
                        disabled={isBusy}
                      >
                        {isSaving ? 'Saving...' : 'Save'}
                      </Button>
                    )}
                    <Button
                      appearance="secondary"
                      onClick={() => void handleApply(item.initializer_name)}
                      disabled={isBusy}
                    >
                      {isApplying ? 'Applying...' : 'Apply now'}
                    </Button>
                    {item.source !== 'baseline' && (
                      <Button
                        appearance="subtle"
                        onClick={() => void handleReset(item.initializer_name)}
                        disabled={isBusy}
                      >
                        {isResetting ? 'Resetting...' : 'Reset saved'}
                      </Button>
                    )}
                  </div>
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}
