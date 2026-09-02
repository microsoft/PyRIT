import {
  Checkbox,
  Field,
  Input,
  Select,
} from '@fluentui/react-components'

import type { Parameter } from '@/types'

import { useParameterFieldStyles } from './ParameterField.styles'
import { getParameterControlKind, type ParameterFormValue } from './parameterForm'

export interface ParameterFieldProps {
  parameter: Parameter
  value: ParameterFormValue
  disabled: boolean
  onChange: (name: string, value: ParameterFormValue) => void
  /** Prefix for `data-testid` attributes. Defaults to `'param'` (e.g. `param-<name>`). */
  testIdPrefix?: string
}

/**
 * Renders the appropriate Fluent UI control for a declared {@link Parameter},
 * driven by {@link getParameterControlKind}. Shared by every dynamic
 * parameter form (initializers, scenario launch) so a parameter always looks
 * and behaves the same way regardless of where it's rendered.
 *
 * A boolean parameter renders as a tri-state select (unset / True / False)
 * rather than a switch, so "not set" (omit — use the server default) stays
 * distinguishable from an explicitly chosen `False`.
 */
export default function ParameterField({
  parameter,
  value,
  disabled,
  onChange,
  testIdPrefix = 'param',
}: ParameterFieldProps) {
  const styles = useParameterFieldStyles()
  const kind = getParameterControlKind(parameter)
  const label = parameter.required ? `${parameter.name} *` : parameter.name
  const testId = `${testIdPrefix}-${parameter.name}`

  if (kind === 'boolean') {
    const current = value === 'true' || value === 'false' ? value : ''
    return (
      <Field label={label} hint={parameter.description ?? undefined}>
        <Select
          className={styles.control}
          value={current}
          disabled={disabled}
          onChange={(_, data) => onChange(parameter.name, data.value)}
          data-testid={testId}
        >
          <option value="">Use default / not set</option>
          <option value="true">True</option>
          <option value="false">False</option>
        </Select>
      </Field>
    )
  }

  if (kind === 'multiselect') {
    const selected = Array.isArray(value) ? value : []
    return (
      <Field label={label} hint={parameter.description ?? undefined}>
        <div className={styles.checkboxGroup} role="group" aria-labelledby={`${testId}-group-label`}>
          <span id={`${testId}-group-label`} className={styles.srOnly}>
            {label}
          </span>
          {(parameter.choices ?? []).map((choice) => {
            const choiceId = `${testId}-${encodeURIComponent(choice)}`
            const choiceLabelId = `${choiceId}-label`
            return (
              <Checkbox
                className={styles.selectionControl}
                key={choice}
                id={choiceId}
                aria-labelledby={choiceLabelId}
                label={{ children: choice, id: choiceLabelId }}
                checked={selected.includes(choice)}
                disabled={disabled}
                onChange={(_, data) => {
                  const next = data.checked
                    ? [...selected, choice]
                    : selected.filter((entry) => entry !== choice)
                  onChange(parameter.name, next)
                }}
                data-testid={`${testId}-${choice}`}
              />
            )
          })}
        </div>
      </Field>
    )
  }

  const stringValue = typeof value === 'string' ? value : ''

  if (kind === 'select') {
    return (
      <Field label={label} hint={parameter.description ?? undefined}>
        <Select
          className={styles.control}
          value={stringValue}
          disabled={disabled}
          onChange={(_, data) => onChange(parameter.name, data.value)}
          data-testid={testId}
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

  const placeholder = typeof parameter.default === 'string' ? parameter.default : undefined
  const hint =
    parameter.description ?? (kind === 'list' ? 'Comma-separated list of values.' : parameter.type_name)

  return (
    <Field label={label} hint={hint}>
      <Input
        className={styles.control}
        value={stringValue}
        type={kind === 'number' ? 'number' : 'text'}
        placeholder={placeholder}
        disabled={disabled}
        onChange={(_, data) => onChange(parameter.name, data.value)}
        data-testid={testId}
      />
    </Field>
  )
}
