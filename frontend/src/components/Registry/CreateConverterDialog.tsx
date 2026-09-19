import { useEffect, useMemo, useState } from 'react'

import {
  Button,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
  Dropdown,
  Field,
  Input,
  MessageBar,
  MessageBarBody,
  Option,
  OptionGroup,
  Select,
  Spinner,
  Switch,
  Text,
} from '@fluentui/react-components'

import { convertersApi, targetsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { ConverterInstance, ConverterTypeEntry, Parameter, TargetInstance } from '@/types'

import { useCreateConverterDialogStyles } from './Registry.styles'

const HIDDEN_CONVERTER_TYPES = new Set(['SelectiveTextConverter'])

function formatDataType(dataType: string): string {
  const value = dataType.replace('_path', '').replace(/_/g, ' ')
  return value.charAt(0).toUpperCase() + value.slice(1)
}

function getModalityLabel(converterType: ConverterTypeEntry): string {
  const inputs = converterType.supported_input_types.length > 0
    ? converterType.supported_input_types.map(formatDataType).join(', ')
    : 'Any'
  const outputs = converterType.supported_output_types.length > 0
    ? converterType.supported_output_types.map(formatDataType).join(', ')
    : 'Any'
  return `${inputs} to ${outputs}`
}

interface CreateConverterDialogProps {
  open: boolean
  onClose: () => void
  onCreated: (converterId: string) => void
}

interface ParameterInputProps {
  parameter: Parameter
  referenceOptions: Array<{ name: string; type: string }>
  value: string
  showError: boolean
  onChange: (value: string) => void
  onBrowse: () => void
}

function parameterDefaultValue(parameter: Parameter): string {
  if (Array.isArray(parameter.default)) {
    return parameter.default.join(', ')
  }
  return parameter.default ?? ''
}

function ParameterInput({
  parameter,
  referenceOptions,
  value,
  showError,
  onChange,
  onBrowse,
}: ParameterInputProps) {
  const styles = useCreateConverterDialogStyles()
  const label = `${parameter.name}${parameter.required ? ' *' : ''}`

  if (parameter.reference_type) {
    return (
      <Field
        label={label}
        hint={`Select a registered ${parameter.reference_type}.`}
        validationMessage={showError ? 'Required' : undefined}
      >
        <Select value={value} onChange={(_, data) => onChange(data.value)}>
          <option value="">Select a registered {parameter.reference_type}</option>
          {referenceOptions.map((option) => (
            <option key={option.name} value={option.name}>
              {option.name} ({option.type})
            </option>
          ))}
        </Select>
      </Field>
    )
  }

  if (parameter.type_name === 'bool') {
    const checked = (value || parameterDefaultValue(parameter) || 'false').toLowerCase() === 'true'
    return (
      <Field label={label} validationMessage={showError ? 'Required' : undefined}>
        <Switch
          checked={checked}
          label={checked ? 'True' : 'False'}
          onChange={(_, data) => onChange(data.checked ? 'true' : 'false')}
        />
      </Field>
    )
  }

  if (parameter.choices?.length) {
    return (
      <Field label={label} validationMessage={showError ? 'Required' : undefined}>
        <Select value={value || parameterDefaultValue(parameter)} onChange={(_, data) => onChange(data.value)}>
          {parameter.required && !parameter.default && <option value="">Select a value</option>}
          {parameter.choices.map((choice) => (
            <option key={choice} value={choice}>{choice}</option>
          ))}
        </Select>
      </Field>
    )
  }

  const isFile = parameter.type_name === 'Path'
    || /path|file/i.test(parameter.name)
    || /path|file/i.test(parameter.description ?? '')

  return (
    <Field
      label={label}
      hint={parameter.description || parameter.type_name}
      validationMessage={showError ? 'Required' : undefined}
    >
      {isFile ? (
        <div className={styles.fileRow}>
          <Input
            className={styles.fileInput}
            value={value}
            placeholder={parameterDefaultValue(parameter) || 'Upload a file or enter a server path'}
            onChange={(_, data) => onChange(data.value)}
          />
          <Button type="button" onClick={onBrowse}>Upload</Button>
        </div>
      ) : (
        <Input
          value={value}
          placeholder={parameterDefaultValue(parameter) || undefined}
          onChange={(_, data) => onChange(data.value)}
        />
      )}
    </Field>
  )
}

export default function CreateConverterDialog({
  open,
  onClose,
  onCreated,
}: CreateConverterDialogProps) {
  const styles = useCreateConverterDialogStyles()
  const [converterTypes, setConverterTypes] = useState<ConverterTypeEntry[]>([])
  const [targets, setTargets] = useState<TargetInstance[]>([])
  const [converters, setConverters] = useState<ConverterInstance[]>([])
  const [selectedType, setSelectedType] = useState('')
  const [registryName, setRegistryName] = useState('')
  const [nameEdited, setNameEdited] = useState(false)
  const [parameterValues, setParameterValues] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [showValidation, setShowValidation] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    let cancelled = false
    Promise.resolve()
      .then(() => {
        if (cancelled) return null
        setLoading(true)
        setError(null)
        return Promise.all([
          convertersApi.listConverterTypes(),
          targetsApi.listTargets(200),
          convertersApi.listConverters(),
        ])
      })
      .then((responses) => {
        if (!responses) return
        const [response, targetResponse, converterResponse] = responses
        if (!cancelled) {
          setConverterTypes(
            response.items.filter((item) => !HIDDEN_CONVERTER_TYPES.has(item.converter_type)),
          )
          setTargets(targetResponse.items)
          setConverters(converterResponse.items)
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setConverterTypes([])
          setTargets([])
          setConverters([])
          setError(toApiError(err).detail)
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [open])

  const selectedConverterType = useMemo(
    () => converterTypes.find((item) => item.converter_type === selectedType),
    [converterTypes, selectedType],
  )
  const groupedConverterTypes = useMemo(() => {
    const groups = new Map<string, ConverterTypeEntry[]>()
    for (const converterType of converterTypes) {
      const label = getModalityLabel(converterType)
      groups.set(label, [...(groups.get(label) ?? []), converterType])
    }
    return [...groups.entries()]
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([label, items]) => ({
        label,
        items: [...items].sort((left, right) =>
          left.converter_type.localeCompare(right.converter_type)),
      }))
  }, [converterTypes])

  const referenceOptions = (parameter: Parameter): Array<{ name: string; type: string }> => {
    if (parameter.reference_type === 'target') {
      return targets.map((target) => ({
        name: target.target_registry_name,
        type: target.identifier.class_name,
      }))
    }
    if (parameter.reference_type === 'converter') {
      return converters.map((converter) => ({
        name: converter.converter_id,
        type: converter.identifier.class_name,
      }))
    }
    return []
  }

  const reset = () => {
    setSelectedType('')
    setRegistryName('')
    setNameEdited(false)
    setParameterValues({})
    setShowValidation(false)
    setError(null)
  }

  const close = () => {
    reset()
    onClose()
  }

  const selectType = (converterType: string) => {
    setSelectedType(converterType)
    if (!nameEdited) setRegistryName(converterType)
    const typeEntry = converterTypes.find((item) => item.converter_type === converterType)
    setParameterValues(
      Object.fromEntries(
        (typeEntry?.parameters ?? [])
          .filter((parameter) => parameter.default != null)
          .map((parameter) => [parameter.name, parameterDefaultValue(parameter)]),
      ),
    )
    setShowValidation(false)
    setError(null)
  }

  const browse = (parameterName: string) => {
    const input = document.createElement('input')
    input.type = 'file'
    input.onchange = () => {
      const file = input.files?.[0]
      if (!file) return
      const reader = new FileReader()
      reader.onload = () => {
        setParameterValues((current) => ({
          ...current,
          [parameterName]: String(reader.result ?? ''),
        }))
      }
      reader.readAsDataURL(file)
    }
    input.click()
  }

  const submit = async () => {
    const missingParameters = (selectedConverterType?.parameters ?? []).some(
      (parameter) => parameter.required
        && !parameter.default
        && !parameterValues[parameter.name]?.trim(),
    )
    if (!selectedType || !registryName.trim() || missingParameters) {
      setShowValidation(true)
      return
    }

    setSubmitting(true)
    setError(null)
    try {
      const response = await convertersApi.createConverter({
        name: registryName.trim(),
        type: selectedType,
        params: parameterValues,
      })
      reset()
      onCreated(response.converter_id)
    } catch (err) {
      setError(toApiError(err).detail)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(_, data) => { if (!data.open) close() }}>
      <DialogSurface className={styles.surface}>
        <DialogBody>
          <DialogTitle>Add Converter</DialogTitle>
          <DialogContent className={styles.content}>
            <form
              className={styles.form}
              onSubmit={(event) => {
                event.preventDefault()
                void submit()
              }}
            >
              {error && (
                <MessageBar intent="error">
                  <MessageBarBody>{error}</MessageBarBody>
                </MessageBar>
              )}
              {loading && <Spinner label="Loading converter types..." />}
              {!loading && converterTypes.length === 0 && !error && (
                <Text>No converter types are available.</Text>
              )}
              {!loading && converterTypes.length > 0 && (
                <>
                  <Field
                    label="Converter type"
                    hint="Converter types are grouped by input and output modality."
                    required
                    validationMessage={showValidation && !selectedType ? 'Select a converter type' : undefined}
                  >
                    <Dropdown
                      aria-label="Converter type"
                      className={styles.typeDropdown}
                      listbox={{ className: styles.typeListbox }}
                      placeholder="Select a converter type"
                      positioning={{
                        align: 'start',
                        matchTargetSize: 'width',
                        position: 'below',
                      }}
                      selectedOptions={selectedType ? [selectedType] : []}
                      value={selectedType}
                      onOptionSelect={(_, data) => {
                        if (data.optionValue) selectType(data.optionValue)
                      }}
                    >
                      {groupedConverterTypes.map((group) => (
                        <OptionGroup key={group.label} label={group.label}>
                          {group.items.map((item) => {
                            const description = item.description || 'No description is available.'
                            const accessibleDescription = [
                              item.converter_type,
                              description,
                              group.label,
                              item.is_llm_based ? 'LLM' : undefined,
                            ].filter((value): value is string => Boolean(value)).join('. ')

                            return (
                              <Option
                                aria-label={accessibleDescription}
                                key={item.converter_type}
                                text={item.converter_type}
                                value={item.converter_type}
                                data-testid={`converter-type-option-${item.converter_type}`}
                              >
                                <div className={styles.typeOption}>
                                  <div className={styles.typeOptionHeader}>
                                    <Text weight="semibold">{item.converter_type}</Text>
                                    {item.is_llm_based && <span className={styles.llmBadge}>LLM</span>}
                                  </div>
                                  <Text size={200} className={styles.typeDescription}>
                                    {description}
                                  </Text>
                                </div>
                              </Option>
                            )
                          })}
                        </OptionGroup>
                      ))}
                    </Dropdown>
                  </Field>
                  {selectedConverterType && (
                    <div className={styles.selectedTypeSummary}>
                      <div className={styles.selectedTypeHeader}>
                        <Text weight="semibold">{selectedConverterType.converter_type}</Text>
                        {selectedConverterType.is_llm_based && (
                          <span className={styles.llmBadge}>LLM</span>
                        )}
                      </div>
                      <Text>{selectedConverterType.description || 'No description is available.'}</Text>
                      <Text size={200} className={styles.typeMetadata}>
                        {getModalityLabel(selectedConverterType)}
                      </Text>
                    </div>
                  )}
                  <Field
                    label="Registry name"
                    required
                    hint="The unique name used to select this configured converter."
                    validationMessage={
                      showValidation && !registryName.trim() ? 'Enter a registry name' : undefined
                    }
                  >
                    <Input
                      value={registryName}
                      onChange={(_, data) => {
                        setRegistryName(data.value)
                        setNameEdited(true)
                      }}
                    />
                  </Field>
                  <div className={styles.parameterGrid}>
                    {selectedConverterType?.parameters.map((parameter) => (
                      <div key={parameter.name} className={styles.parameterRow}>
                        <ParameterInput
                          parameter={parameter}
                          referenceOptions={referenceOptions(parameter)}
                          value={parameterValues[parameter.name] ?? ''}
                          showError={
                            showValidation
                            && parameter.required
                            && !parameter.default
                            && !parameterValues[parameter.name]?.trim()
                          }
                          onChange={(value) => setParameterValues((current) => ({
                            ...current,
                            [parameter.name]: value,
                          }))}
                          onBrowse={() => browse(parameter.name)}
                        />
                      </div>
                    ))}
                  </div>
                </>
              )}
            </form>
          </DialogContent>
          <DialogActions>
            <Button appearance="secondary" onClick={close}>Cancel</Button>
            <Button
              appearance="primary"
              disabled={loading || submitting || converterTypes.length === 0}
              onClick={() => void submit()}
            >
              {submitting ? 'Adding...' : 'Add Converter'}
            </Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  )
}
