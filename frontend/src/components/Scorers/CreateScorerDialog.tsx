import { useEffect, useState } from 'react'

import {
  Button, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle,
  Input, MessageBar, MessageBarBody, Select, Spinner,
} from '@fluentui/react-components'

import HelpField from '@/components/HelpField'
import ParameterField from '@/components/Parameters/ParameterField'
import ReferenceField from '@/components/Parameters/ReferenceField'
import { buildParametersFromForm, getInitialFormValues, type ParameterFormValue } from '@/components/Parameters/parameterForm'
import { scorersApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { Parameter, ScorerInstance, TargetInstance } from '@/types'
import { useTechniqueEditorStyles } from '@/components/Techniques/TechniqueEditor.styles'

interface CreateScorerDialogProps {
  targets: TargetInstance[]
  scorers: ScorerInstance[]
  onClose: () => void
  onCreated: (scorer: ScorerInstance) => void
}

const SCALAR_TYPES = new Set(['str', 'int', 'float', 'bool', 'list[str]', 'list[int]', 'list[float]', 'list[bool]'])

/** Builds real registered scorers from the shared constructor metadata. */
export default function CreateScorerDialog({ targets, scorers, onClose, onCreated }: CreateScorerDialogProps) {
  const styles = useTechniqueEditorStyles()
  const [types, setTypes] = useState<Array<{ scorer_type: string; parameters: Parameter[] }>>([])
  const [type, setType] = useState('')
  const [name, setName] = useState('')
  const [values, setValues] = useState<Record<string, ParameterFormValue>>({})
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const parameters = types.find((entry) => entry.scorer_type === type)?.parameters ?? []
  const unsupported = parameters.filter((parameter) => parameter.required
    && !SCALAR_TYPES.has(parameter.type_name) && !parameter.reference_type)
  const referenceValue = (name: string): string | string[] => {
    const value = values[name]
    return typeof value === 'string' || Array.isArray(value) ? value : ''
  }

  useEffect(() => {
    let active = true
    void scorersApi.listTypes().then((response) => {
      if (active) setTypes(response.items.filter((entry) => entry.supports_objective_scoring))
    }).catch((failure: unknown) => {
      if (active) setError(toApiError(failure).detail)
    }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [])

  async function createScorer(): Promise<void> {
    const result = buildParametersFromForm(parameters.filter((parameter) => !parameter.reference_type), values)
    if (!result.ok) { setError(result.error); return }
    const params: Record<string, unknown> = { ...result.parameters }
    for (const parameter of parameters.filter((entry) => entry.reference_type)) {
      const value = values[parameter.name]
      if (parameter.required && (!value || Array.isArray(value) && value.length === 0)) {
        setError(`Select ${parameter.name}.`); return
      }
      if (value) params[parameter.name] = value
    }
    setSubmitting(true)
    setError(null)
    try {
      onCreated(await scorersApi.createScorer({ name: name.trim(), type, params }))
    } catch (failure: unknown) {
      setError(toApiError(failure).detail)
    } finally { setSubmitting(false) }
  }

  return <Dialog open onOpenChange={(_, data) => { if (!data.open && !submitting) onClose() }}>
    <DialogSurface><DialogBody>
      <DialogTitle>Create objective scorer</DialogTitle>
      <DialogContent>
        {loading && <Spinner label="Loading scorer types" />}
        {error && <MessageBar intent="error"><MessageBarBody role="alert">{error}</MessageBarBody></MessageBar>}
        {!loading && types.length === 0 && !error && <MessageBar intent="warning"><MessageBarBody role="alert">
          No objective scorer types can be created here. Register a true/false scorer explicitly in Python.
        </MessageBarBody></MessageBar>}
        <HelpField label="Scorer name" required>
          <Input value={name} disabled={submitting} onChange={(_, data) => setName(data.value)} />
        </HelpField>
        {unsupported.length > 0 && <MessageBar intent="warning"><MessageBarBody role="alert">
          Configure {unsupported.map((parameter) => parameter.name).join(', ')} in Python and register this scorer.
          These required values are not supported by this form.
        </MessageBarBody></MessageBar>}
        <HelpField label="Scorer type" required>
          <Select value={type} disabled={submitting} onChange={(_, data) => {
            setType(data.value)
            setValues(getInitialFormValues(types.find((entry) => entry.scorer_type === data.value)?.parameters ?? []))
          }}>
            <option value="">Select a scorer type</option>
            {types.map((entry) => <option key={entry.scorer_type}>{entry.scorer_type}</option>)}
          </Select>
        </HelpField>
        {parameters.filter((parameter) => parameter.reference_type || SCALAR_TYPES.has(parameter.type_name)).map((parameter) =>
          parameter.reference_type ? <ReferenceField key={parameter.name} label={parameter.name}
            help={parameter.description ?? undefined} multiple={parameter.is_list} disabled={submitting}
            options={parameter.reference_type === 'target'
              ? targets.map((target) => ({ name: target.target_registry_name, type: target.identifier.class_name }))
              : scorers.map((scorer) => ({ name: scorer.scorer_registry_name, type: scorer.identifier.class_name }))}
            value={referenceValue(parameter.name)}
            onChange={(value) => setValues((current) => ({ ...current, [parameter.name]: value }))} />
          : <ParameterField key={parameter.name} parameter={parameter} value={values[parameter.name] ?? ''}
            disabled={submitting} onChange={(key, value) => setValues((current) => ({ ...current, [key]: value }))} />)}
      </DialogContent>
      <DialogActions>
        <Button className={styles.action} disabled={submitting} onClick={onClose}>Cancel</Button>
        <Button className={styles.action} appearance="primary" disabled={!type || !name.trim() || loading || submitting || unsupported.length > 0}
          onClick={() => { void createScorer() }}>Create scorer</Button>
      </DialogActions>
    </DialogBody></DialogSurface>
  </Dialog>
}
