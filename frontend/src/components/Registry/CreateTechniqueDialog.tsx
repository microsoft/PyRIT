import { useEffect, useRef, useState } from 'react'

import {
  Button, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle,
  Field, Input, MessageBar, MessageBarBody, Select, Spinner, Textarea,
} from '@fluentui/react-components'

import ParameterField from '@/components/Parameters/ParameterField'
import ReferenceField from '@/components/Parameters/ReferenceField'
import { buildParametersFromForm, getInitialFormValues, type ParameterFormValue } from '@/components/Parameters/parameterForm'
import { convertersApi, targetsApi, techniquesApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { CreateTechniqueRequest, Parameter, RegistryReferenceOption, TechniqueTypeEntry } from '@/types'
import { fetchAllPages } from '@/utils/fetchAllPages'

import { useCreateTechniqueDialogStyles } from './CreateTechniqueDialog.styles'

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

interface CreateTechniqueDialogProps {
  onClose: () => void
  onCreated: () => void
}

/** Configure existing attacks with registry references. Advanced factories use Python. */
export default function CreateTechniqueDialog({ onClose, onCreated }: CreateTechniqueDialogProps) {
  const styles = useCreateTechniqueDialogStyles()
  const [types, setTypes] = useState<TechniqueTypeEntry[]>([])
  const [targetOptions, setTargetOptions] = useState<RegistryReferenceOption[]>([])
  const [converterOptions, setConverterOptions] = useState<RegistryReferenceOption[]>([])
  const [selectedType, setSelectedType] = useState('')
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [tags, setTags] = useState('')
  const [values, setValues] = useState<Record<string, ParameterFormValue>>({})
  const [requestConverters, setRequestConverters] = useState<string[]>([])
  const [responseConverters, setResponseConverters] = useState<string[]>([])
  const [adversarialTarget, setAdversarialTarget] = useState('')
  const [systemPrompt, setSystemPrompt] = useState('')
  const [seedPrompt, setSeedPrompt] = useState('')
  const [turnPrompt, setTurnPrompt] = useState('')
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
          techniquesApi.listTypes(),
          fetchAllPages(
            (cursor: string | undefined) => targetsApi.listTargets(200, cursor),
            undefined,
            (entry) => entry.target_registry_name,
            true,
          ),
          convertersApi.listConverters(),
        ])
        if (ignore) return
        setTypes(metadata.items.filter((entry) =>
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
  }, [retry])

  useEffect(() => {
    if (submitError && error) errorRef.current?.focus()
  }, [error, submitError])

  const selected = types.find((entry) => entry.attack_type === selectedType)
  const parameters = guiParameters(getAttackParameters(selected))

  const submit = async (): Promise<void> => {
    const fail = (message: string): void => { setError(message); setSubmitError(true) }
    const parsedTags = tags.split(',').map((tag) => tag.trim()).filter(Boolean)
    if (!selected) {
      fail('Select an attack type.')
      return
    }
    const result = buildParametersFromForm(parameters, values)
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
      await techniquesApi.createTechnique(request)
      if (active.current) onCreated()
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
          <DialogTitle>New technique</DialogTitle>
          <DialogContent className={styles.content}>
            <div className={styles.form}>
              {loading && <Spinner label="Loading technique types..." />}
              {error && <MessageBar intent="error" ref={errorRef} tabIndex={-1}><MessageBarBody>{error}</MessageBarBody></MessageBar>}
              {!loading && types.length === 0 && <Button onClick={() => setRetry(retry + 1)}>Retry metadata</Button>}
              {!loading && types.length > 0 && (
                <>
                  <Field label="Registry name" required hint="Start with a letter. Use letters, digits, and underscores.">
                    <Input value={name} disabled={submitting} onChange={(_, data) => setName(data.value)} />
                  </Field>
                  <Field label="Description"><Textarea value={description} disabled={submitting} onChange={(_, data) => setDescription(data.value)} /></Field>
                  <Field label="Tags" hint="Comma-separated tags.">
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
                  </Field>
                  {parameters.map((parameter) => (
                    <ParameterField key={parameter.name} parameter={parameter} value={values[parameter.name] ?? ''}
                      disabled={submitting} allowEmptyList
                      onChange={(key, value) => setValues({ ...values, [key]: value })} />
                  ))}
                  {selected?.supports_converters && (
                    <>
                      <ReferenceField label="Request converters" options={converterOptions} value={requestConverters} multiple disabled={submitting}
                        onChange={(next) => { if (Array.isArray(next)) setRequestConverters(next) }} />
                      <ReferenceField label="Response converters" options={converterOptions} value={responseConverters} multiple disabled={submitting}
                        onChange={(next) => { if (Array.isArray(next)) setResponseConverters(next) }} />
                    </>
                  )}
                  {selected?.supports_adversarial && (
                    <>
                      <ReferenceField label="Adversarial target" options={targetOptions} value={adversarialTarget} disabled={submitting}
                        onChange={(next) => { if (typeof next === 'string') setAdversarialTarget(next) }} />
                      <Field label="Adversarial system prompt"><Textarea value={systemPrompt} disabled={submitting} onChange={(_, data) => setSystemPrompt(data.value)} /></Field>
                      <Field label="Adversarial seed prompt"><Textarea value={seedPrompt} disabled={submitting} onChange={(_, data) => setSeedPrompt(data.value)} /></Field>
                      <Field label="Adversarial per-turn prompt"><Textarea value={turnPrompt} disabled={submitting} onChange={(_, data) => setTurnPrompt(data.value)} /></Field>
                    </>
                  )}
                </>
              )}
            </div>
          </DialogContent>
          <DialogActions>
            <Button className={styles.action} onClick={onClose}>Cancel</Button>
            <Button className={styles.action} appearance="primary" disabled={loading || submitting || !name.trim() || !selected}
              onClick={() => { void submit() }}>{submitting ? 'Adding...' : 'Add technique'}</Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  )
}
