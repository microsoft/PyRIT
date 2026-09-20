import { useEffect, useMemo, useRef, useState } from 'react'

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
import ParameterField from '@/components/Parameters/ParameterField'
import {
  buildParametersFromForm,
  getInitialFormValues,
  isStructuredParameterFormValue,
  type ParameterFormValue,
} from '@/components/Parameters/parameterForm'

import { useCreateConverterDialogStyles } from './Registry.styles'

const HIDDEN_CONVERTER_TYPES = new Set(['SelectiveTextConverter'])
const EDITABLE_PARAMETER_TYPES = new Set([
  'str', 'int', 'float', 'bool', 'Path', 'list[str]', 'list[int]', 'list[float]', 'list[bool]',
])

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

// Where a message came from decides whether it takes the keyboard: a failed
// submission answers something the user just did, while a metadata-loading
// failure arrives unprompted and must not pull focus out of the form.
interface DialogError {
  message: string
  fromSubmit: boolean
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

function isEditableParameter(parameter: Parameter): boolean {
  if (parameter.reference_type || parameter.choices?.length) return true

  const members: string[] = []
  let member = ''
  let depth = 0
  for (const character of parameter.type_name) {
    if (character === '|' && depth === 0) {
      members.push(member.trim())
      member = ''
      continue
    }
    if (character === '[') depth++
    else if (character === ']') depth--
    member += character
  }
  members.push(member.trim())

  // Mixed unions can use the text input only when they explicitly accept strings.
  return members.some((type) => EDITABLE_PARAMETER_TYPES.has(type) && (members.length === 1 || type === 'str'))
}

function parameterDefaultValue(parameter: Parameter): string {
  if (Array.isArray(parameter.default)) {
    return parameter.default.join(', ')
  }
  return parameter.default ?? ''
}

function formValueIsSet(value: ParameterFormValue | undefined): boolean {
  if (isStructuredParameterFormValue(value)) {
    return Boolean(value.type)
  }
  return typeof value === 'string' ? Boolean(value.trim()) : Boolean(value?.length)
}

function stringFormValue(value: ParameterFormValue | undefined): string {
  return typeof value === 'string' ? value : ''
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

  if (!isEditableParameter(parameter)) {
    return (
      <Field label={label} hint="This parameter cannot be configured here. Omit it to use the converter default.">
        <Input disabled value="" />
      </Field>
    )
  }

  const isFile = parameter.type_name === 'Path'
    || parameter.type_name === 'Path | str'
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
            placeholder={parameterDefaultValue(parameter) || (
              parameter.type_name === 'Path | str'
                ? 'Upload a file or enter a URL'
                : 'Upload a file or enter a server path'
            )}
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
  const [parameterValues, setParameterValues] = useState<Record<string, ParameterFormValue>>({})
  const [loading, setLoading] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [showValidation, setShowValidation] = useState(false)
  const [error, setError] = useState<DialogError | null>(null)
  const errorRef = useRef<HTMLDivElement>(null)
  // The dialog instance is reused across openings, so a create response can
  // land after the opening that started it has gone.
  const openEpochRef = useRef(0)

  useEffect(() => {
    // Every change of `open` ends the opening before it, so a response from the
    // previous one leaves this opening's own state alone.
    openEpochRef.current += 1
    if (!open) return
    let cancelled = false
    Promise.resolve()
      .then(() => {
        if (cancelled) return null
        setLoading(true)
        setError(null)
        // A request from the previous opening keeps its own "Adding..." state, so
        // clear it here rather than letting that response clear it for this one.
        setSubmitting(false)
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
          setError({ message: toApiError(err).detail, fromSubmit: false })
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [open])

  // Hand the keyboard to the failure once React has committed it. A frame
  // callback can run before the render that adds the message bar, and focusing
  // from there finds no node and silently does nothing, leaving the keyboard on
  // the primary action where the request left it.
  useEffect(() => {
    if (!error?.fromSubmit) return
    errorRef.current?.focus()
  }, [error])

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
    const parameters = typeEntry?.parameters ?? []
    setParameterValues({
      ...getInitialFormValues(parameters.filter((parameter) => parameter.variants)),
      ...Object.fromEntries(
        parameters
          .filter((parameter) => !parameter.variants
            && isEditableParameter(parameter) && parameter.default != null)
          .map((parameter) => [parameter.name, parameterDefaultValue(parameter)]),
      ),
    })
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
        && !formValueIsSet(parameterValues[parameter.name]),
    )
    if (!selectedType || !registryName.trim() || missingParameters) {
      setShowValidation(true)
      return
    }

    const parameters = selectedConverterType?.parameters ?? []
    const params = Object.fromEntries(
      Object.entries(parameterValues).filter(([, value]) => !isStructuredParameterFormValue(value)),
    )
    const structured = buildParametersFromForm(
      parameters.filter((parameter) => parameter.variants),
      parameterValues,
    )
    if (!structured.ok) {
      // Not tagged as a submission failure: nothing was disabled, so the keyboard
      // is still on the primary action and has nothing to be restored from.
      setError({ message: structured.error, fromSubmit: false })
      return
    }
    if (structured.parameters) {
      Object.assign(params, structured.parameters)
    }

    const epoch = openEpochRef.current
    setSubmitting(true)
    setError(null)
    try {
      const response = await convertersApi.createConverter({
        name: registryName.trim(),
        type: selectedType,
        params,
      })
      // Only the opening this request was submitted from is cleared: a response
      // that outlived its opening must not wipe the form the user is filling in
      // now. onCreated stays ungated so a late success still refreshes the list.
      if (openEpochRef.current === epoch) {
        reset()
      }
      onCreated(response.converter_id)
    } catch (err) {
      // A failure from an opening the user has already left stays out of the
      // one in front of them, and out of its focus.
      if (openEpochRef.current === epoch) {
        setError({ message: toApiError(err).detail, fromSubmit: true })
      }
    } finally {
      if (openEpochRef.current === epoch) {
        setSubmitting(false)
      }
    }
  }

  // Disabled, but still focusable: a browser runs the unfocusing steps when the
  // primary action is disabled for the request, which drops focus to <body> and
  // out of the open dialog, and Escape then stops dismissing it because Tabster
  // handles that key on the dialog surface. aria-disabled still blocks a second
  // submit, because Fluent drops the click and key handlers instead.
  const submitDisabled = loading || submitting || converterTypes.length === 0

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
                  <MessageBarBody ref={errorRef} tabIndex={-1} role="alert">{error.message}</MessageBarBody>
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
                        {parameter.variants ? (
                          <ParameterField
                            parameter={parameter}
                            value={parameterValues[parameter.name] ?? ''}
                            disabled={submitting}
                            showRequiredError={showValidation && parameter.required}
                            testIdPrefix="structured"
                            onChange={(name, value) => setParameterValues((current) => ({
                              ...current,
                              [name]: value,
                            }))}
                          />
                        ) : <ParameterInput
                          parameter={parameter}
                          referenceOptions={referenceOptions(parameter)}
                          value={stringFormValue(parameterValues[parameter.name])}
                          showError={
                            showValidation
                            && parameter.required
                            && !parameter.default
                            && !formValueIsSet(parameterValues[parameter.name])
                          }
                          onChange={(value) => setParameterValues((current) => ({
                            ...current,
                            [parameter.name]: value,
                          }))}
                          onBrowse={() => browse(parameter.name)}
                        />}
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
              disabled={submitDisabled}
              disabledFocusable={submitDisabled}
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
