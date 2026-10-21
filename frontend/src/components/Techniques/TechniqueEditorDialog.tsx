import { useEffect, useRef, useState } from 'react'

import {
  Button, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle,
  Field, Input, MessageBar, MessageBarBody, Select, Spinner, Text, Textarea,
} from '@fluentui/react-components'

import ParameterField from '@/components/Parameters/ParameterField'
import ReferenceField from '@/components/Parameters/ReferenceField'
import { buildParametersFromForm, getInitialFormValues, type ParameterFormValue } from '@/components/Parameters/parameterForm'
import { convertersApi, targetsApi, techniquesApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type {
  CreateTechniqueRequest, Parameter, RegistryReferenceOption, TechniqueTypeEntry,
  TechniqueConfiguration, TechniqueChanges, TechniqueEditResponse, SimulationChanges,
} from '@/types'
import { fetchAllPages } from '@/utils/fetchAllPages'

import { useTechniqueEditorDialogStyles } from './TechniqueEditorDialog.styles'

const SCALAR_TYPES = new Set(['str', 'int', 'float', 'bool', 'list[str]', 'list[int]', 'list[float]', 'list[bool]'])

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

interface TechniqueEditorDialogProps {
  onClose: () => void
  onCreated?: () => void
  /** Both configurations remain source-pinned. The initial value can include applied run-local edits. */
  original?: TechniqueConfiguration
  initial?: TechniqueConfiguration
  onApplied?: (result: TechniqueEditResponse) => void
}

/** Shared creation and run-local editor. Edit mode never registers a technique. */
export default function TechniqueEditorDialog({ onClose, onCreated, original, initial, onApplied }: TechniqueEditorDialogProps) {
  const styles = useTechniqueEditorDialogStyles()
  const editing = original !== undefined
  const configuration = initial ?? original
  const [types, setTypes] = useState<TechniqueTypeEntry[]>([])
  const [targetOptions, setTargetOptions] = useState<RegistryReferenceOption[]>([])
  const [converterOptions, setConverterOptions] = useState<RegistryReferenceOption[]>([])
  const [selectedType, setSelectedType] = useState(configuration?.attack_type ?? '')
  const [name, setName] = useState(configuration?.source_name ?? '')
  const [description, setDescription] = useState(configuration?.settings.description ?? '')
  const [tags, setTags] = useState('')
  const [values, setValues] = useState<Record<string, ParameterFormValue>>(() =>
    configuration ? editorValues(configuration) : {})
  const [requestConverters, setRequestConverters] = useState<string[]>(configuration?.settings.request_converters ?? [])
  const [responseConverters, setResponseConverters] = useState<string[]>(configuration?.settings.response_converters ?? [])
  const [adversarialTarget, setAdversarialTarget] = useState(configuration?.settings.adversarial_chat ?? '')
  const [systemPrompt, setSystemPrompt] = useState(configuration?.settings.adversarial_system_prompt ?? '')
  const [seedPrompt, setSeedPrompt] = useState(configuration?.settings.adversarial_seed_prompt ?? '')
  const [turnPrompt, setTurnPrompt] = useState(configuration?.settings.adversarial_prompt_template ?? '')
  const [simulation, setSimulation] = useState<SimulationChanges>(configuration?.settings.simulation ?? {})
  const [numTurns, setNumTurns] = useState(String(configuration?.settings.simulation?.num_turns ?? ''))
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [retry, setRetry] = useState(0)
  const active = useRef(true)
  const errorRef = useRef<HTMLDivElement>(null)
  const [submitError, setSubmitError] = useState(false)

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
        setConverterOptions(converters.items.map((entry) => ({ name: entry.converter_id, type: entry.identifier.class_name })))
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

  const submit = async (): Promise<void> => {
    const fail = (message: string): void => { setError(message); setSubmitError(true) }
    const parsedTags = tags.split(',').map((tag) => tag.trim()).filter(Boolean)
    if (!selected) {
      fail('Select an attack type.')
      return
    }
    const originalValues = original ? editorValues(original) : {}
    const changedParameters = original ? parameters.filter((parameter) =>
      JSON.stringify(values[parameter.name]) !== JSON.stringify(originalValues[parameter.name])) : parameters
    const result = buildParametersFromForm(changedParameters, values)
    if (!result.ok) { fail(result.error); return }
    const request: CreateTechniqueRequest = {
      name, description, tags: parsedTags, type: selectedType,
      params: result.parameters ?? {},
    }
    if (selected.supports_converters && (requestConverters.length || responseConverters.length)) {
      request.request_converters = requestConverters
      request.response_converters = responseConverters
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
        if (description !== (original.settings.description ?? '')) changes.description = description
        for (const [key, next] of [
          ['request_converters', requestConverters], ['response_converters', responseConverters],
        ] as const) {
          if (editable(key) && JSON.stringify(next) !== JSON.stringify(original.settings[key] ?? [])) changes[key] = next
        }
        for (const [key, next] of [
          ['adversarial_chat', adversarialTarget], ['adversarial_system_prompt', systemPrompt],
          ['adversarial_seed_prompt', seedPrompt], ['adversarial_prompt_template', turnPrompt],
        ] as const) {
          if (editable(key) && next !== (original.settings[key] ?? '')) changes[key] = next || null
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

  return (
    <Dialog open onOpenChange={(_, data) => { if (!data.open) onClose() }}>
      <DialogSurface className={styles.surface}>
        <DialogBody>
          <DialogTitle>{editing ? 'Edit technique' : 'New technique'}</DialogTitle>
          <DialogContent className={styles.content}>
            <div className={styles.form}>
              {loading && <Spinner label="Loading technique types..." />}
              {error && <MessageBar intent="error" role="alert" ref={errorRef} tabIndex={-1}><MessageBarBody>{error}</MessageBarBody></MessageBar>}
              {!loading && types.length === 0 && <Button onClick={() => setRetry(retry + 1)}>Retry metadata</Button>}
              {!loading && types.length > 0 && (
                <>
                  {editing ? <><Text>Technique: {name}</Text><Text>Attack type: {selectedType}</Text>
                    <Text>Changes apply to this run only. The registered technique stays unchanged.</Text></> : <Field label="Registry name" required hint="Start with a letter. Use letters, digits, and underscores.">
                    <Input value={name} disabled={submitting} onChange={(_, data) => setName(data.value)} />
                  </Field>}
                  <Field label="Description"><Textarea value={description} disabled={submitting} onChange={(_, data) => setDescription(data.value)} /></Field>
                  {original?.settings.simulation && (
                    <section className={styles.section} aria-label="Simulated conversation">
                      <Text as="h3">Simulated conversation</Text>
                      <Text block>Edit generation settings, not a generated transcript. Template placeholders stay literal.</Text>
                      <Field label="Turn count" required><Input type="number" min={1} step={1} value={numTurns}
                        disabled={submitting} onChange={(_, data) => setNumTurns(data.value)} /></Field>
                      {([
                        ['adversarial_chat_system_prompt', 'Simulated user system prompt'],
                        ['simulated_target_system_prompt', 'Simulated assistant system prompt'],
                        ['next_message_system_prompt', 'Next-message system prompt'],
                        ['final_user_message', 'Fixed final user message'],
                      ] as const).filter(([key]) => original.settings.simulation?.[key] != null).map(([key, label]) => (
                        <Field key={key} label={label}><Textarea className={styles.prompt} resize="vertical" value={simulation[key] ?? ''}
                          disabled={submitting} onChange={(_, data) => setSimulation({ ...simulation, [key]: data.value })} /></Field>
                      ))}
                      {original.settings.simulation.next_message_system_prompt == null
                        && original.settings.simulation.final_user_message == null && <Text>No additional user message.</Text>}
                    </section>
                  )}
                  {!editing && <><Field label="Tags" hint="Comma-separated tags.">
                    <Input value={tags} disabled={submitting} onChange={(_, data) => setTags(data.value)} />
                  </Field>
                  <Field label="Attack type" required>
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
                  </Field></>}
                  {parameters.map((parameter) => parameter.reference_type ? (
                    <ReferenceField key={parameter.name} label={parameter.name}
                      options={parameter.reference_type === 'target' ? targetOptions : converterOptions}
                      value={referenceValue(values[parameter.name])} multiple={parameter.is_list} disabled={submitting}
                      onChange={(next) => setValues({ ...values, [parameter.name]: next })} />
                  ) : <ParameterField key={parameter.name} parameter={parameter} value={values[parameter.name] ?? ''}
                      disabled={submitting} allowEmptyList
                      extraHint={editing ? 'Clearing text sets an empty string. Other cleared values must allow null.' : undefined}
                      onChange={(key, value) => setValues({ ...values, [key]: value })} />
                  )}
                  {selected?.supports_converters && (
                    <>
                      {editable('request_converters') && <ReferenceField label="Request converters" options={converterOptions} value={requestConverters} multiple disabled={submitting}
                        onChange={(next) => { if (Array.isArray(next)) setRequestConverters(next) }} />}
                      {editable('response_converters') && <ReferenceField label="Response converters" options={converterOptions} value={responseConverters} multiple disabled={submitting}
                        onChange={(next) => { if (Array.isArray(next)) setResponseConverters(next) }} />}
                    </>
                  )}
                  {(selected?.supports_adversarial || original?.editable_fields.includes('adversarial_chat')) && (
                    <>{editable('adversarial_chat') && <ReferenceField label="Adversarial target" options={targetOptions} value={adversarialTarget} disabled={submitting}
                        onChange={(next) => { if (typeof next === 'string') setAdversarialTarget(next) }} />}
                      {selected?.supports_adversarial && <>
                      {editable('adversarial_system_prompt') && <Field label="Adversarial system prompt"><Textarea value={systemPrompt} disabled={submitting} onChange={(_, data) => setSystemPrompt(data.value)} /></Field>}
                      {editable('adversarial_seed_prompt') && <Field label="Adversarial seed prompt"><Textarea value={seedPrompt} disabled={submitting} onChange={(_, data) => setSeedPrompt(data.value)} /></Field>}
                      {editable('adversarial_prompt_template') && <Field label="Adversarial per-turn prompt"><Textarea value={turnPrompt} disabled={submitting} onChange={(_, data) => setTurnPrompt(data.value)} /></Field>}
                      </>}
                    </>
                  )}
                  {original && <section aria-label="Read-only settings"><Text as="h3">Read-only settings</Text>
                    <dl>{Object.entries(original.read_only).map(([key, value]) => <div key={key}>
                      <dt>{key}</dt><dd>{value}</dd></div>)}</dl></section>}
                </>
              )}
            </div>
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
