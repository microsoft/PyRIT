import { type Dispatch, type SetStateAction, useEffect, useRef, useState } from 'react'

import {
  Badge, Button, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle,
  Input, MessageBar, MessageBarBody, Select, Spinner, Text, Textarea,
} from '@fluentui/react-components'

import ContextualHelpLabel from '@/components/ContextualHelpLabel'
import HelpField from '@/components/HelpField'
import { HELP_DEFINITIONS } from '@/components/helpDefinitions'
import ParameterField from '@/components/Parameters/ParameterField'
import ConverterChainField from '@/components/Converters/ConverterChainField'
import ReferenceField from '@/components/Parameters/ReferenceField'
import { buildParametersFromForm, getInitialFormValues, type ParameterFormValue } from '@/components/Parameters/parameterForm'
import { convertersApi, targetsApi, techniquesApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type {
  CreateTechniqueRequest, Parameter, RegistryReferenceOption, TechniqueTypeEntry,
  TechniqueConfiguration, TechniqueChanges, TechniqueEditResponse, SimulationChanges,
  ConverterInstance, ConverterSelection,
  TechniquePromptField,
} from '@/types'
import { fetchAllPages } from '@/utils/fetchAllPages'

import { useTechniqueEditorStyles } from './TechniqueEditor.styles'

const SCALAR_TYPES = new Set(['str', 'int', 'float', 'bool', 'list[str]', 'list[int]', 'list[float]', 'list[bool]'])
const VALIDATION_DELAY_MS = 300
const PROMPT_LABELS: Record<TechniquePromptField, string> = {
  adversarial_system_prompt: 'Adversarial system prompt',
  adversarial_seed_prompt: 'Adversarial seed prompt',
  adversarial_prompt_template: 'Adversarial per-turn prompt',
}

function canConfigure(parameter: Parameter, allowStructured = true): boolean {
  if (parameter.reference_type) return false
  if (parameter.variants) {
    return allowStructured && !parameter.is_list && Object.values(parameter.variants).some((parameters) =>
      parameters.every((nested) => !nested.required || canConfigure(nested, false)))
  }
  return SCALAR_TYPES.has(parameter.type_name) || Boolean(parameter.choices?.length)
}

function guiParameters(parameters: Parameter[], allowStructured = true): Parameter[] {
  return parameters.filter((parameter) => canConfigure(parameter, allowStructured)).map((parameter) => parameter.variants ? {
    ...parameter,
    variants: Object.fromEntries(Object.entries(parameter.variants)
      .filter(([, nested]) => nested.every((entry) => !entry.required || canConfigure(entry, false)))
      .map(([name, nested]) => [name, guiParameters(nested, false)])),
  } : parameter)
}

function getAttackParameters(type?: TechniqueTypeEntry): Parameter[] {
  return type?.parameters.filter((parameter) => parameter.name !== 'attack_converter_config') ?? []
}

function editorValues(configuration: TechniqueConfiguration): Record<string, ParameterFormValue> {
  const values = getInitialFormValues(configuration.parameters, configuration.settings.params)
  for (const parameter of configuration.parameters) {
    const source = configuration.settings.params?.[parameter.name]
    if (parameter.reference_type && parameter.is_list && Array.isArray(source)) {
      values[parameter.name] = source.filter((item): item is string => typeof item === 'string')
    }
  }
  return values
}

function referenceValue(value: ParameterFormValue | undefined): string | string[] {
  return typeof value === 'string' || Array.isArray(value) ? value : ''
}

function promptValue(configuration: TechniqueConfiguration | undefined, field: TechniquePromptField): string {
  return configuration?.prompt_templates?.[field]?.value ?? configuration?.settings[field] ?? ''
}

interface TechniqueEditorProps {
  onClose?: () => void
  onCreated?: () => void
  /** Both configurations remain source-pinned. The initial value can include applied run-local edits. */
  original?: TechniqueConfiguration
  initial?: TechniqueConfiguration
  onApplied?: (result: TechniqueEditResponse) => void
  autoApply?: boolean
  onPending?: () => void
  disabled?: boolean
}

/** Shared creation and run-local editor. Edit mode never registers a technique. */
export default function TechniqueEditor({
  onClose, onCreated, original, initial, onApplied, autoApply = false, onPending, disabled = false,
}: TechniqueEditorProps) {
  const styles = useTechniqueEditorStyles()
  const editing = original !== undefined
  const configuration = initial ?? original
  const [types, setTypes] = useState<TechniqueTypeEntry[]>([])
  const [targetOptions, setTargetOptions] = useState<RegistryReferenceOption[]>([])
  const [converters, setConverters] = useState<ConverterInstance[]>([])
  const converterOptions = converters.map((entry) => ({ name: entry.converter_id, type: entry.identifier.class_name }))
  const [selectedType, setSelectedType] = useState(configuration?.attack_type ?? '')
  const [name, setName] = useState(configuration?.source_name ?? '')
  const [description, setDescription] = useState(configuration?.settings.description ?? '')
  const [tags, setTags] = useState('')
  const [values, setValues] = useState<Record<string, ParameterFormValue>>(() =>
    configuration ? editorValues(configuration) : {})
  const [requestConverters, setRequestConverters] = useState<ConverterSelection[]>(configuration?.settings.request_converters ?? [])
  const [responseConverters, setResponseConverters] = useState<ConverterSelection[]>(configuration?.settings.response_converters ?? [])
  const [adversarialTarget, setAdversarialTarget] = useState(configuration?.settings.adversarial_chat ?? '')
  const [systemPrompt, setSystemPrompt] = useState(promptValue(configuration, 'adversarial_system_prompt'))
  const [seedPrompt, setSeedPrompt] = useState(promptValue(configuration, 'adversarial_seed_prompt'))
  const [turnPrompt, setTurnPrompt] = useState(promptValue(configuration, 'adversarial_prompt_template'))
  const [simulation, setSimulation] = useState<SimulationChanges>(configuration?.settings.simulation ?? {})
  const [numTurns, setNumTurns] = useState(String(configuration?.settings.simulation?.num_turns ?? ''))
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [retry, setRetry] = useState(0)
  const active = useRef(true)
  const errorRef = useRef<HTMLDivElement>(null)
  const [submitError, setSubmitError] = useState(false)
  const [revision, setRevision] = useState(0)
  const [validating, setValidating] = useState(false)

  function update<Value>(setter: Dispatch<SetStateAction<Value>>, value: Value): void {
    if (autoApply) {
      onPending?.()
      setValidating(true)
      setError(null)
      setRevision((current) => current + 1)
    }
    setter(value)
  }

  useEffect(() => {
    active.current = true
    return () => { active.current = false }
  }, [])

  useEffect(() => {
    let ignore = false
    void Promise.resolve().then(async () => {
      if (ignore) return
      setLoading(true)
      setError(null)
      try {
        const [metadata, targets, converters] = await Promise.all([
          original ? Promise.resolve({ items: [{
            attack_type: original.attack_type, description: '', parameters: original.parameters,
            supports_converters: original.editable_fields.some((field) => field.endsWith('_converters')),
            supports_adversarial: original.editable_fields.some((field) => [
              'adversarial_system_prompt', 'adversarial_seed_prompt', 'adversarial_prompt_template',
            ].includes(field)),
          }] }) : techniquesApi.listTypes(),
          fetchAllPages(
            (cursor: string | undefined) => targetsApi.listTargets(200, cursor),
            undefined,
            (entry) => entry.target_registry_name,
            true,
          ),
          convertersApi.listConverters(),
        ])
        if (ignore) return
        setTypes(original ? metadata.items : metadata.items.filter((entry) =>
          getAttackParameters(entry).every((parameter) => !parameter.required || canConfigure(parameter))))
        setTargetOptions(targets.map((entry) => ({ name: entry.target_registry_name, type: entry.identifier.class_name })))
        setConverters(converters.items)
      } catch (err) {
        if (!ignore) setError(toApiError(err).detail)
      } finally {
        if (!ignore) setLoading(false)
      }
    })
    return () => { ignore = true }
  }, [retry, original])

  useEffect(() => {
    if (submitError && error) errorRef.current?.focus()
  }, [error, submitError])

  const selected = types.find((entry) => entry.attack_type === selectedType)
  const parameters = original ? original.parameters : guiParameters(getAttackParameters(selected))
  const editable = (field: string): boolean => !original || original.editable_fields.includes(field)

  const editChanges = (): TechniqueChanges => {
    if (!original) throw new Error('A source technique is required for editing.')
    const originalValues = editorValues(original)
    const changedParameters = parameters.filter((parameter) =>
      JSON.stringify(values[parameter.name]) !== JSON.stringify(originalValues[parameter.name]))
    const result = buildParametersFromForm(changedParameters, values)
    if (!result.ok) throw new Error(result.error)
    const changes: TechniqueChanges = {}
    if (changedParameters.length) {
      changes.params = { ...result.parameters }
      for (const parameter of changedParameters) {
        if (!Object.prototype.hasOwnProperty.call(changes.params, parameter.name)) {
          changes.params[parameter.name] = parameter.type_name === 'str' && !parameter.choices?.length
            && !parameter.reference_type ? '' : null
        }
      }
    }
    for (const [key, next] of [
      ['request_converters', requestConverters], ['response_converters', responseConverters],
    ] as const) {
      if (editable(key) && JSON.stringify(next) !== JSON.stringify(original.settings[key] ?? [])) changes[key] = next
    }
    if (editable('adversarial_chat') && adversarialTarget !== (original.settings.adversarial_chat ?? '')) {
      changes.adversarial_chat = adversarialTarget || null
    }
    for (const [key, next] of [
      ['adversarial_system_prompt', systemPrompt], ['adversarial_seed_prompt', seedPrompt],
      ['adversarial_prompt_template', turnPrompt],
    ] as const) {
      if (editable(key) && next !== promptValue(original, key)) {
        if (!next.trim()) throw new Error(
          `${PROMPT_LABELS[key]} must contain non-empty text. Use Reset to default to restore the selected technique's prompt.`,
        )
        changes[key] = next
      }
    }
    if (original.settings.simulation) {
      const turns = Number(numTurns)
      if (!Number.isInteger(turns) || turns < 1) throw new Error('Turn count must be a positive integer.')
      const simulationChanges: SimulationChanges = {}
      if (turns !== original.settings.simulation.num_turns) simulationChanges.num_turns = turns
      for (const key of [
        'adversarial_chat_system_prompt', 'simulated_target_system_prompt',
        'next_message_system_prompt', 'final_user_message',
      ] as const) {
        if (simulation[key] !== original.settings.simulation[key]) simulationChanges[key] = simulation[key]
      }
      if (Object.keys(simulationChanges).length) changes.simulation = simulationChanges
    }
    return changes
  }

  let changes: TechniqueChanges | undefined
  let editError: string | undefined
  if (original) {
    try {
      changes = editChanges()
    } catch (err) {
      editError = toApiError(err).detail
    }
  }
  const changesJson = changes === undefined ? undefined : JSON.stringify(changes)

  useEffect(() => {
    if (!autoApply || !original || revision === 0) return
    if (editError || changesJson === undefined) return
    let ignore = false
    const timer = window.setTimeout(() => {
      void techniquesApi.validateEdit({
        source_name: original.source_name, source_identity: original.source_identity,
        changes: JSON.parse(changesJson) as TechniqueChanges, dependencies: {},
      }).then((response) => {
        if (!ignore) onApplied?.(response)
      }).catch((err: unknown) => {
        if (!ignore) setError(toApiError(err).detail)
      }).finally(() => {
        if (!ignore) setValidating(false)
      })
    }, VALIDATION_DELAY_MS)
    return () => { ignore = true; window.clearTimeout(timer) }
  }, [autoApply, original, revision, changesJson, editError, onApplied])

  const submit = async (): Promise<void> => {
    const fail = (message: string): void => { setError(message); setSubmitError(true) }
    const parsedTags = tags.split(',').map((tag) => tag.trim()).filter(Boolean)
    if (!selected) { fail('Select an attack type.'); return }
    const result = buildParametersFromForm(parameters, values)
    if (!original && !result.ok) { fail(result.error); return }
    const request: CreateTechniqueRequest = {
      name, description, tags: parsedTags, type: selectedType,
      params: result.ok ? result.parameters ?? {} : {},
    }
    if (selected.supports_converters && (requestConverters.length || responseConverters.length)) {
      request.request_converters = requestConverters.filter((entry): entry is string => typeof entry === 'string')
      request.response_converters = responseConverters.filter((entry): entry is string => typeof entry === 'string')
    }
    if (selected.supports_adversarial) {
      if (adversarialTarget) request.adversarial_chat = adversarialTarget
      if (systemPrompt) request.adversarial_system_prompt = systemPrompt
      if (seedPrompt) request.adversarial_seed_prompt = seedPrompt
      if (turnPrompt) request.adversarial_prompt_template = turnPrompt
    }
    setSubmitting(true)
    setError(null)
    setSubmitError(false)
    try {
      if (original) {
        if (editError || changes === undefined) throw new Error(editError ?? 'Technique settings are invalid.')
        const response = await techniquesApi.validateEdit({
          source_name: original.source_name, source_identity: original.source_identity, changes, dependencies: {},
        })
        if (active.current) onApplied?.(response)
      } else {
        await techniquesApi.createTechnique(request)
        if (active.current) onCreated?.()
      }
    } catch (err) {
      if (active.current) fail(toApiError(err).detail)
    } finally {
      if (active.current) setSubmitting(false)
    }
  }

  const inputsDisabled = disabled || submitting
  const displayedError = autoApply && revision > 0 ? editError ?? error : error
  const content = (
            <div className={styles.form} onKeyDown={(event) => {
              if (autoApply && event.key === 'Enter' && event.target instanceof HTMLInputElement
                && event.currentTarget.contains(event.target)) event.preventDefault()
            }}>
              {loading && <Spinner label="Loading technique types..." />}
              {displayedError && <MessageBar intent="error" role="alert" ref={errorRef} tabIndex={-1}><MessageBarBody>{displayedError}</MessageBarBody></MessageBar>}
              {!loading && types.length === 0 && <Button onClick={() => setRetry(retry + 1)}>Retry metadata</Button>}
              {!loading && types.length > 0 && (
                <>
                  {!editing && <HelpField label="Registry name" required hint="Start with a letter. Use letters, digits, and underscores."
                    help="A unique name used to find and reuse this technique. Adding a technique does not run an attack.">
                    <Input value={name} disabled={submitting} onChange={(_, data) => setName(data.value)} />
                  </HelpField>}
                  {editing
                    ? description && <Text>{description}</Text>
                    : <HelpField label="Description" help="A short note about the technique's purpose or setup, to help you choose it later.">
                      <Textarea value={description} disabled={inputsDisabled} onChange={(_, data) => setDescription(data.value)} />
                    </HelpField>}
                  {original?.settings.simulation && (
                    <div className={styles.section}>
                      <ContextualHelpLabel term="Simulated conversation">
                        Generated messages added before the final prompt. These are sample exchanges, not replies from the objective target.
                      </ContextualHelpLabel>
                      <HelpField label="Turn count" required
                        help="Number of turns generated for the sample conversation before the final prompt. These are preparation turns, not live turns with the objective target.">
                        <Input type="number" min={1} step={1} value={numTurns}
                          disabled={inputsDisabled} onChange={(_, data) => update(setNumTurns, data.value)} />
                      </HelpField>
                      {([
                        ['adversarial_chat_system_prompt', 'Simulated user system prompt',
                          'Instructions for the model that writes the user side of the sample conversation. {{ objective }} inserts the test objective.'],
                        ['simulated_target_system_prompt', 'Simulated assistant system prompt',
                          'Instructions for the model that writes the assistant side of the sample conversation. These generated replies do not come from the objective target.'],
                        ['next_message_system_prompt', 'Next-message system prompt',
                          'Instructions for writing the final user message after the sample conversation. {{ conversation_context }} inserts the generated history.'],
                        ['final_user_message', 'Fixed final user message',
                          'A specific final user message sent after the sample conversation, instead of a model-generated final message.'],
                      ] as const).filter(([key]) => original.settings.simulation?.[key] != null).map(([key, label, help]) => (
                        <HelpField key={key} label={label} help={help}>
                          <Textarea className={styles.prompt} resize="vertical" value={simulation[key] ?? ''}
                            disabled={inputsDisabled} onChange={(_, data) => update(setSimulation, { ...simulation, [key]: data.value })} />
                        </HelpField>
                      ))}
                    </div>
                  )}
                  {!editing && <><HelpField label="Tags" hint="Comma-separated tags."
                    help="Labels used to find and group techniques, such as single_turn or simulation. Tags do not change the attack algorithm.">
                    <Input value={tags} disabled={submitting} onChange={(_, data) => setTags(data.value)} />
                  </HelpField>
                  <HelpField label="Attack type" required help="The algorithm this technique configures. It manages how prompts are sent and how the attack conversation proceeds.">
                    <Select className={styles.select} value={selectedType} disabled={submitting} onChange={(_, data) => {
                      setSelectedType(data.value)
                      setValues(getInitialFormValues(
                        guiParameters(getAttackParameters(types.find((entry) => entry.attack_type === data.value))),
                        null, { prefillDefaults: false },
                      ))
                    }}>
                      <option value="">Select an attack</option>
                      {types.map((entry) => <option key={entry.attack_type} value={entry.attack_type}>{entry.attack_type}</option>)}
                    </Select>
                  </HelpField></>}
                  {parameters.map((parameter) => parameter.reference_type ? (
                    <ReferenceField key={parameter.name} label={parameter.name} help={parameter.description ?? undefined}
                      options={parameter.reference_type === 'target' ? targetOptions : converterOptions}
                      value={referenceValue(values[parameter.name])} multiple={parameter.is_list} disabled={inputsDisabled}
                      onChange={(next) => update(setValues, { ...values, [parameter.name]: next })} />
                  ) : <ParameterField key={parameter.name} parameter={parameter} value={values[parameter.name] ?? ''}
                      disabled={inputsDisabled} allowEmptyList
                      onChange={(key, value) => update(setValues, { ...values, [key]: value })} />
                  )}
                  {selected?.supports_converters && (
                    <>
                      {editable('request_converters') && <ConverterChainField label="Request converters" converters={converters}
                        help={HELP_DEFINITIONS.requestConverters}
                        value={requestConverters} disabled={inputsDisabled} allowTemporary={editing}
                        onChange={(next) => update(setRequestConverters, next)} onConvertersChanged={setConverters} />}
                      {editable('response_converters') && <ConverterChainField label="Response converters" converters={converters}
                        help={HELP_DEFINITIONS.responseConverters}
                        value={responseConverters} disabled={inputsDisabled} allowTemporary={editing}
                        onChange={(next) => update(setResponseConverters, next)} onConvertersChanged={setConverters} />}
                    </>
                  )}
                  {(selected?.supports_adversarial || original?.editable_fields.includes('adversarial_chat')) && (
                    <>{!editing && <ReferenceField label="Adversarial target" options={targetOptions} value={adversarialTarget} disabled={inputsDisabled}
                        help={HELP_DEFINITIONS.adversarialTarget}
                        onChange={(next) => { if (typeof next === 'string') update(setAdversarialTarget, next) }} />}
                      {selected?.supports_adversarial && <>
                      {([
                        ['adversarial_system_prompt', systemPrompt, setSystemPrompt,
                          "Instructions that guide the attack-writing model. These are not the objective target's system instructions."],
                        ['adversarial_seed_prompt', seedPrompt, setSeedPrompt,
                          'The starting message for the attack-writing model, used to prepare its first attack prompt.'],
                        ['adversarial_prompt_template', turnPrompt, setTurnPrompt,
                          'Instructions used to request the next attack prompt on each turn. The attack supplies the current conversation context.'],
                      ] as const).map(([field, value, setter, help]) => {
                        if (!editable(field)) return null
                        const label = PROMPT_LABELS[field]
                        const template = original?.prompt_templates?.[field]
                        const customized = value !== promptValue(original, field)
                        return <HelpField key={field} label={label} help={help}>
                          {template && <div className={styles.promptControls}>
                            <Badge appearance="tint" color={customized ? 'brand' : 'informative'}>{customized ? 'Customized' : 'Default'}</Badge>
                            <Button size="small" appearance="subtle" disabled={inputsDisabled || !customized}
                              aria-label={`Reset ${label.toLowerCase()} to default`}
                              onClick={() => update(setter, promptValue(original, field))}>Reset to default</Button>
                          </div>}
                          <Textarea className={styles.prompt} resize="vertical" value={value}
                            disabled={inputsDisabled} onChange={(_, data) => update(setter, data.value)} />
                          {template && template.parameters.length > 0 && <Text size={200}>
                            Template variables: {template.parameters.map((parameter) => `{{ ${parameter} }}`).join(', ')}
                          </Text>}
                          {field === 'adversarial_system_prompt' && configuration?.system_prompt_prefix
                            && <Text size={200} className={styles.promptPrefix}>
                              Additional guidance (read-only; prepended at execution):{'\n'}{configuration.system_prompt_prefix}
                            </Text>}
                        </HelpField>
                      })}
                      </>}
                    </>
                  )}
                </>
              )}
              {autoApply && validating && !editError && <Spinner size="tiny" label="Validating technique settings..." />}
              {autoApply && !validating && revision > 0 && !displayedError && <Text role="status">Technique settings applied to this run.</Text>}
            </div>
  )

  if (autoApply) return content
  return (
    <Dialog open onOpenChange={(_, data) => { if (!data.open) onClose?.() }}>
      <DialogSurface className={styles.surface}>
        <DialogBody>
          <DialogTitle>{editing ? 'Edit technique' : 'New technique'}</DialogTitle>
          <DialogContent className={styles.content}>
            {content}
          </DialogContent>
          <DialogActions>
            <Button className={styles.action} onClick={onClose}>Cancel</Button>
            <Button className={styles.action} appearance="primary" disabled={loading || submitting || !name.trim() || !selected}
              onClick={() => { void submit() }}>{submitting ? editing ? 'Applying...' : 'Adding...' : editing ? 'Apply to run' : 'Add technique'}</Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  )
}
