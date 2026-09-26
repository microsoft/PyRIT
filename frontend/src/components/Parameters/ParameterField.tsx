import {
  Checkbox,
  Field,
  Input,
  Select,
  Textarea,
} from '@fluentui/react-components'

import type { Parameter } from '@/types'

import { useParameterFieldStyles } from './ParameterField.styles'
import {
  getInitialFormValues,
  getParameterControlKind,
  isStructuredParameterFormValue,
  parseJsonObjectFormValue,
  type ParameterFormValue,
} from './parameterForm'

export interface ParameterFieldProps {
  parameter: Parameter
  value: ParameterFormValue
  disabled: boolean
  onChange: (name: string, value: ParameterFormValue) => void
  /** Optional presentation label when the parameter name should remain unchanged for submission. */
  label?: string
  /** Present the declared default as guidance while keeping the field unset. */
  showDefaultHint?: boolean
  /** Let a list field distinguish an explicit empty list from an omitted value. */
  allowEmptyList?: boolean
  /** Show required validation for a structured input whose variant is unset. */
  showRequiredError?: boolean
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
  label: labelOverride,
  showDefaultHint = false,
  allowEmptyList = false,
  showRequiredError = false,
  testIdPrefix = 'param',
}: ParameterFieldProps) {
  const styles = useParameterFieldStyles()
  const kind = getParameterControlKind(parameter)
  const label = parameter.required
    ? `${labelOverride ?? parameter.name} *`
    : labelOverride ?? parameter.name
  const testId = `${testIdPrefix}-${parameter.name}`
  const defaultText = Array.isArray(parameter.default)
    ? parameter.default.join(', ')
    : parameter.default
  const hasDisplayDefault = defaultText != null && defaultText !== ''
  const defaultHint = showDefaultHint && hasDisplayDefault
    ? `Defaults to ${defaultText}.`
    : null
  const descriptiveHint = [parameter.description, defaultHint]
    .filter((part): part is string => Boolean(part))
    .join(' ')

  if (kind === 'structured') {
    const current = isStructuredParameterFormValue(value) ? value : { type: '', values: {} }
    return (
      <>
        <Field
          label={label}
          hint={descriptiveHint || undefined}
          validationMessage={showRequiredError && !current.type ? 'Required' : undefined}
        >
          <Select
            className={styles.control}
            value={current.type}
            disabled={disabled}
            onChange={(_, data) => onChange(parameter.name, {
              type: data.value,
              values: getInitialFormValues(parameter.variants?.[data.value] ?? []),
            })}
            data-testid={testId}
          >
            <option value="">Use default / not set</option>
            {Object.keys(parameter.variants ?? {}).map((type) => (
              <option key={type} value={type}>{type}</option>
            ))}
          </Select>
        </Field>
        {(parameter.variants?.[current.type] ?? []).map((nested) => (
          <ParameterField
            key={nested.name}
            parameter={nested}
            value={current.values[nested.name] ?? ''}
            disabled={disabled}
            allowEmptyList
            testIdPrefix={`${testIdPrefix}-${parameter.name}`}
            onChange={(name, nestedValue) => onChange(parameter.name, {
              ...current,
              values: { ...current.values, [name]: nestedValue },
            })}
          />
        ))}
      </>
    )
  }

  if (kind === 'json') {
    const current = typeof value === 'string' ? value : ''
    const parsed = current.trim()
      ? parseJsonObjectFormValue(current, label.replace(/ \*$/, ''))
      : null
    const validationMessage = parsed && !parsed.ok ? parsed.error : undefined
    const jsonHint = [descriptiveHint, 'Enter a JSON object.']
      .filter((part): part is string => Boolean(part))
      .join(' ')
    return (
      <Field
        label={label}
        hint={jsonHint}
        validationMessage={validationMessage}
        validationState={validationMessage ? 'error' : 'none'}
      >
        <Textarea
          className={styles.control}
          value={current}
          placeholder={hasDisplayDefault
            ? showDefaultHint ? `Defaults to ${defaultText}` : String(defaultText)
            : '{"key": "value"}'}
          disabled={disabled}
          resize="vertical"
          onChange={(_, data) => onChange(parameter.name, data.value)}
          data-testid={testId}
        />
      </Field>
    )
  }

  if (kind === 'boolean') {
    const current = value === 'true' || value === 'false' ? value : ''
    return (
      <Field label={label} hint={descriptiveHint || undefined}>
        <Select
          className={styles.control}
          value={current}
          disabled={disabled}
          onChange={(_, data) => onChange(parameter.name, data.value)}
          data-testid={testId}
        >
          <option value="">
            {defaultHint ? `Use default (${defaultText})` : 'Use default / not set'}
          </option>
          <option value="true">True</option>
          <option value="false">False</option>
        </Select>
      </Field>
    )
  }

  if (kind === 'multiselect') {
    const selected = Array.isArray(value) ? value : []
    return (
      <Field label={label} hint={descriptiveHint || undefined}>
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
  const emptyListSelected = allowEmptyList && kind === 'list' && Array.isArray(value) && value.length === 0

  if (kind === 'select') {
    return (
      <Field label={label} hint={descriptiveHint || undefined}>
        <Select
          className={styles.control}
          value={stringValue}
          disabled={disabled}
          onChange={(_, data) => onChange(parameter.name, data.value)}
          data-testid={testId}
        >
          <option value="">
            {defaultHint ? `Use default (${defaultText})` : 'Select a value'}
          </option>
          {(parameter.choices ?? []).map((choice) => (
            <option key={choice} value={choice}>
              {choice}
            </option>
          ))}
        </Select>
      </Field>
    )
  }

  const placeholder = hasDisplayDefault
    ? showDefaultHint ? `Defaults to ${defaultText}` : defaultText
    : undefined
  const fallbackHint = kind === 'list' ? 'Comma-separated list of values.' : parameter.type_name
  const hint = descriptiveHint || fallbackHint

  return (
    <>
      <Field label={label} hint={hint}>
        <Input
          className={styles.control}
          value={stringValue}
          type={kind === 'number' ? 'number' : 'text'}
          placeholder={placeholder}
          disabled={disabled || emptyListSelected}
          onChange={(_, data) => onChange(parameter.name, data.value)}
          data-testid={testId}
        />
      </Field>
      {kind === 'list' && allowEmptyList && (
        <Checkbox
          label={`Use empty list for ${parameter.name}`}
          checked={emptyListSelected}
          disabled={disabled}
          onChange={(_, data) => onChange(parameter.name, data.checked ? [] : '')}
        />
      )}
    </>
  )
}
