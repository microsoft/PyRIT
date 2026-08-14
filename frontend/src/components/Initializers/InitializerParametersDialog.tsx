import { useRef, useState } from 'react'
import {
  Button,
  Checkbox,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
  Field,
  Input,
  Select,
  Switch,
  Text,
} from '@fluentui/react-components'

import type { Parameter, RegisteredInitializer } from '@/types'

import { useAdditionalInitializersStyles } from './AdditionalInitializers.styles'
import {
  buildParametersFromForm,
  getInitialFormValues,
  getParameterControlKind,
  type ParameterFormValue,
} from './initializerParameterForm'

interface InitializerParametersDialogProps {
  open: boolean
  mode: 'add' | 'edit'
  initializer: RegisteredInitializer | null
  initialParameters?: Record<string, unknown> | null
  submitting?: boolean
  externalError?: string | null
  onSubmit: (parameters: Record<string, unknown> | null) => void | Promise<void>
  onOpenChange: (open: boolean) => void
}

export default function InitializerParametersDialog({
  open,
  mode,
  initializer,
  initialParameters = null,
  submitting = false,
  externalError = null,
  onSubmit,
  onOpenChange,
}: InitializerParametersDialogProps) {
  const styles = useAdditionalInitializersStyles()
  const parameters = initializer?.supported_parameters ?? []
  const [values, setValues] = useState<Record<string, ParameterFormValue>>(() =>
    getInitialFormValues(parameters, initialParameters),
  )
  const [error, setError] = useState<string | null>(null)
  const submitInProgressRef = useRef(false)

  const acceptsParameters = parameters.length > 0

  const updateValue = (name: string, value: ParameterFormValue): void => {
    setValues((prev) => ({ ...prev, [name]: value }))
    setError(null)
  }

  const handleSubmit = async (): Promise<void> => {
    submitInProgressRef.current = true
    try {
      await submitForm()
    } finally {
      submitInProgressRef.current = false
    }
  }

  const submitForm = async (): Promise<void> => {
    if (!acceptsParameters) {
      setError(null)
      await onSubmit(null)
      return
    }

    const result = buildParametersFromForm(parameters, values)
    if (!result.ok) {
      setError(result.error)
      return
    }

    setError(null)
    await onSubmit(result.parameters)
  }

  const initializerName = initializer?.initializer_name ?? ''
  const title = mode === 'add' ? `Add ${initializerName} initializer` : `Edit ${initializerName} initializer`
  const submitLabel = mode === 'add' ? 'Add' : 'Save'

  return (
    <Dialog
      open={open}
      onOpenChange={(_, data) => {
        if (!data.open && submitInProgressRef.current) {
          return
        }
        onOpenChange(data.open)
      }}
    >
      <DialogSurface>
        <DialogBody>
          <DialogTitle>{title}</DialogTitle>
          <DialogContent className={styles.dialogContent}>
            {initializer && (
              <>
                <Text size={300}>{initializer.description || 'No description available.'}</Text>
                {initializer.required_env_vars.length > 0 && (
                  <Text size={200} className={styles.envVarText}>
                    Required env vars: {initializer.required_env_vars.join(', ')}
                  </Text>
                )}
              </>
            )}
            {acceptsParameters ? (
              <div className={styles.parameterFields}>
                {parameters.map((parameter) => (
                  <ParameterField
                    key={parameter.name}
                    parameter={parameter}
                    value={values[parameter.name]}
                    disabled={submitting}
                    onChange={updateValue}
                  />
                ))}
              </div>
            ) : (
              <Text size={300} className={styles.parameterHint}>
                This initializer takes no parameters.
              </Text>
            )}
            {(error || externalError) && (
              <Text role="alert" className={styles.errorText}>
                {error || externalError}
              </Text>
            )}
          </DialogContent>
          <DialogActions>
            <Button appearance="secondary" onClick={() => onOpenChange(false)} disabled={submitting}>
              Cancel
            </Button>
            <Button
              appearance="primary"
              onClick={() => void handleSubmit()}
              disabled={submitting || !initializer}
            >
              {submitting ? `${submitLabel}...` : submitLabel}
            </Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  )
}

interface ParameterFieldProps {
  parameter: Parameter
  value: ParameterFormValue
  disabled: boolean
  onChange: (name: string, value: ParameterFormValue) => void
}

function ParameterField({ parameter, value, disabled, onChange }: ParameterFieldProps) {
  const styles = useAdditionalInitializersStyles()
  const kind = getParameterControlKind(parameter)
  const label = parameter.required ? `${parameter.name} *` : parameter.name

  if (kind === 'boolean') {
    const checked = value === 'true'
    return (
      <Field label={label} hint={parameter.description ?? undefined}>
        <Switch
          checked={checked}
          label={checked ? 'True' : 'False'}
          disabled={disabled}
          onChange={(_, data) => onChange(parameter.name, data.checked ? 'true' : 'false')}
          data-testid={`param-${parameter.name}`}
        />
      </Field>
    )
  }

  if (kind === 'multiselect') {
    const selected = Array.isArray(value) ? value : []
    return (
      <Field label={label} hint={parameter.description ?? undefined}>
        <div className={styles.checkboxGroup} role="group" aria-label={parameter.name}>
          {(parameter.choices ?? []).map((choice) => (
            <Checkbox
              key={choice}
              id={`param-${parameter.name}-${choice}`}
              label={choice}
              checked={selected.includes(choice)}
              disabled={disabled}
              onChange={(_, data) => {
                const next = data.checked
                  ? [...selected, choice]
                  : selected.filter((entry) => entry !== choice)
                onChange(parameter.name, next)
              }}
              data-testid={`param-${parameter.name}-${choice}`}
            />
          ))}
        </div>
      </Field>
    )
  }

  const stringValue = typeof value === 'string' ? value : ''

  if (kind === 'select') {
    return (
      <Field label={label} hint={parameter.description ?? undefined}>
        <Select
          value={stringValue}
          disabled={disabled}
          onChange={(_, data) => onChange(parameter.name, data.value)}
          data-testid={`param-${parameter.name}`}
        >
          <option value="">Select a value</option>
          {(parameter.choices ?? []).map((choice) => (
            <option key={choice} value={choice}>
              {choice}
            </option>
          ))}
        </Select>
      </Field>
    )
  }

  const hint =
    parameter.description ?? (kind === 'list' ? 'Comma-separated list of values.' : parameter.type_name)

  return (
    <Field label={label} hint={hint}>
      <Input
        value={stringValue}
        type={kind === 'number' ? 'number' : 'text'}
        placeholder={parameter.default ?? undefined}
        disabled={disabled}
        onChange={(_, data) => onChange(parameter.name, data.value)}
        data-testid={`param-${parameter.name}`}
      />
    </Field>
  )
}
