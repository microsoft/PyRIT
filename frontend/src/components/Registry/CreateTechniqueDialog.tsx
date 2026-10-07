import { useEffect, useRef, useState } from 'react'

import {
  Button, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle,
  Field, Input, MessageBar, MessageBarBody, Select, Spinner, Text, Textarea,
} from '@fluentui/react-components'

import ParameterField from '@/components/Parameters/ParameterField'
import ReferenceField from '@/components/Parameters/ReferenceField'
import { buildParametersFromForm, getInitialFormValues, type ParameterFormValue } from '@/components/Parameters/parameterForm'
import { convertersApi, scorersApi, targetsApi, techniquesApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { CreateTechniqueRequest, PaginationInfo, Parameter, ParameterReferenceOptions, TechniqueTypeEntry } from '@/types'

import { useCreateTechniqueDialogStyles } from './CreateTechniqueDialog.styles'

const SCALAR_TYPES = new Set(['str', 'int', 'float', 'bool', 'list[str]', 'list[int]', 'list[float]', 'list[bool]'])

function canConfigure(parameter: Parameter): boolean {
  if (parameter.reference_type) return parameter.reference_type !== 'scenario'
  if (parameter.variants) {
    return !parameter.is_list && Object.values(parameter.variants).some((parameters) =>
      parameters.every((nested) => !nested.required || canConfigure(nested)))
  }
  return SCALAR_TYPES.has(parameter.type_name) || Boolean(parameter.choices?.length)
}

async function loadReferencePages<T>(
  load: (cursor?: string) => Promise<{ items: T[]; pagination: PaginationInfo }>,
): Promise<T[]> {
  const items: T[] = []
  const seen = new Set<string>()
  let cursor: string | undefined
  do {
    const page = await load(cursor)
    items.push(...page.items)
    if (!page.pagination.has_more) return items
    const next = page.pagination.next_cursor
    if (!next || seen.has(next)) throw new Error('The registry returned an invalid page cursor.')
    seen.add(next)
    cursor = next
  } while (cursor)
  return items
}

function guiParameters(parameters: Parameter[]): Parameter[] {
  return parameters.filter(canConfigure).map((parameter) => parameter.variants ? {
    ...parameter,
    variants: Object.fromEntries(Object.entries(parameter.variants)
      .filter(([, nested]) => nested.every((entry) => !entry.required || canConfigure(entry)))
      .map(([name, nested]) => [name, guiParameters(nested)])),
  } : parameter)
}

interface CreateTechniqueDialogProps {
  onClose: () => void
  onCreated: () => void
}

/** Configure existing attacks with registry references. Advanced factories use Python. */
export default function CreateTechniqueDialog({ onClose, onCreated }: CreateTechniqueDialogProps) {
  const styles = useCreateTechniqueDialogStyles()
  const [types, setTypes] = useState<TechniqueTypeEntry[]>([])
  const [references, setReferences] = useState<ParameterReferenceOptions>({ target: [], converter: [], scorer: [], scenario: [] })
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
        const [metadata, targets, converters, scorers] = await Promise.all([
          techniquesApi.listTypes(),
          loadReferencePages((cursor) => targetsApi.listTargets(200, cursor)),
          convertersApi.listConverters(),
          loadReferencePages((cursor) => scorersApi.listScorers(cursor)),
        ])
        if (ignore) return
        setTypes(metadata.items)
        setReferences({
          target: targets.map((entry) => ({ name: entry.target_registry_name, type: entry.identifier.class_name })),
          converter: converters.items.map((entry) => ({ name: entry.converter_id, type: entry.identifier.class_name })),
          scorer: scorers.map((entry) => ({ name: entry.scorer_registry_name, type: entry.identifier.class_name })),
          scenario: [],
        })
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
  const attackParameters = selected?.parameters.filter((parameter) => parameter.name !== 'attack_converter_config') ?? []
  const unsupportedRequired = attackParameters.filter((parameter) => parameter.required && !canConfigure(parameter))
  const parameters = guiParameters(attackParameters)

  const submit = async (): Promise<void> => {
    const fail = (message: string): void => { setError(message); setSubmitError(true) }
    const parsedTags = tags.split(',').map((tag) => tag.trim()).filter(Boolean)
    if (!selected || unsupportedRequired.length) {
      fail('This attack needs inputs that cannot be set in this form. Use a Python initializer.')
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
              <Text>Runtime only. Restart or reinitialize PyRIT to remove runtime additions.</Text>
              {loading && <Spinner label="Loading technique types..." />}
              {error && <MessageBar intent="error" ref={errorRef} tabIndex={-1}><MessageBarBody>{error}</MessageBarBody></MessageBar>}
              {!loading && types.length === 0 && <Button onClick={() => setRetry(retry + 1)}>Retry metadata</Button>}
              {!loading && types.length > 0 && (
                <>
                  <Field label="Registry name" required hint="Start with a letter. Use letters, digits, and underscores.">
                    <Input value={name} disabled={submitting} onChange={(_, data) => setName(data.value)} />
                  </Field>
                  <Field label="Description"><Textarea value={description} disabled={submitting} onChange={(_, data) => setDescription(data.value)} /></Field>
                  <Field label="Tags" hint="Comma-separated tags. No default, core, or light tag is added automatically.">
                    <Input value={tags} disabled={submitting} onChange={(_, data) => setTags(data.value)} />
                  </Field>
                  <Field label="Attack type" required>
                    <Select value={selectedType} disabled={submitting} onChange={(_, data) => {
                      setSelectedType(data.value)
                      setValues(getInitialFormValues(types.find((entry) => entry.attack_type === data.value)?.parameters ?? [], null, { prefillDefaults: false }))
                    }}>
                      <option value="">Select an attack</option>
                      {types.map((entry) => <option key={entry.attack_type} value={entry.attack_type}>{entry.attack_type}</option>)}
                    </Select>
                  </Field>
                  {selected && <Text>{selected.description}</Text>}
                  {selected && <Text>This form shows supported basic inputs. Seeds, conversations, and advanced configurations need a Python initializer.</Text>}
                  {attackParameters.filter((parameter) => !canConfigure(parameter)).map((parameter) => (
                    <Text key={parameter.name}>{parameter.name}: {parameter.required ? 'Required input' : 'Optional input'} cannot be set here. Use Python.</Text>
                  ))}
                  {parameters.map((parameter) => (
                    <ParameterField key={parameter.name} parameter={parameter} value={values[parameter.name] ?? ''}
                      disabled={submitting} referenceOptions={references} allowEmptyList
                      onChange={(key, value) => setValues({ ...values, [key]: value })} />
                  ))}
                  {selected?.supports_converters && (
                    <>
                      <ReferenceField label="Request converters" options={references.converter} value={requestConverters} multiple disabled={submitting}
                        onChange={(next) => { if (Array.isArray(next)) setRequestConverters(next) }} />
                      <ReferenceField label="Response converters" options={references.converter} value={responseConverters} multiple disabled={submitting}
                        onChange={(next) => { if (Array.isArray(next)) setResponseConverters(next) }} />
                    </>
                  )}
                  {selected?.supports_adversarial && (
                    <>
                      <ReferenceField label="Adversarial target" options={references.target} value={adversarialTarget} disabled={submitting}
                        hint="Not set: resolve the default adversarial target at execution. The objective target is selected when you run a scenario."
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
            <Button className={styles.action} appearance="primary" disabled={loading || submitting || !name.trim() || !selected || unsupportedRequired.length > 0}
              onClick={() => { void submit() }}>{submitting ? 'Adding...' : 'Add technique'}</Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  )
}
