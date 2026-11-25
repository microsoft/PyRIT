import type { ChangeEvent } from 'react'

import { Field, Select, Spinner, type SelectOnChangeData } from '@fluentui/react-components'

import type { PromptGeneratorTargetRole, TargetInstance } from '@/types'
import { targetOptionLabel } from '@/utils/targetIdentity'

import { usePromptGenerationStyles } from './PromptGeneration.styles'

interface GenerationTargetFieldsProps {
  readonly roles: readonly PromptGeneratorTargetRole[]
  readonly targets: readonly TargetInstance[]
  readonly loading: boolean
  readonly error: string | null
  readonly value: Readonly<Record<string, string>>
  readonly onChange: (value: Record<string, string>) => void
  readonly disabled?: boolean
}

/** One target picker per generator role, chosen from the target registry; the server checks capabilities. */
export default function GenerationTargetFields({
  roles,
  targets,
  loading,
  error,
  value,
  onChange,
  disabled = false,
}: GenerationTargetFieldsProps) {
  const styles = usePromptGenerationStyles()
  if (loading) return <Spinner size="tiny" label="Loading targets..." />

  return (
    <div className={styles.fieldGrid}>
      {roles.map((role) => {
        const selected = value[role.role] ?? ''
        const unregistered = selected !== '' && !targets.some((target) => target.target_registry_name === selected)
        const validation = error && targets.length === 0
          ? error
          : unregistered ? `"${selected}" is not a registered target.` : null
        return (
          <Field
            key={role.role}
            label={role.label}
            required={role.required}
            hint={role.description}
            validationMessage={validation}
            validationState={validation ? 'error' : 'none'}
          >
            <Select
              className={styles.control}
              value={selected}
              disabled={disabled}
              data-testid={`generation-target-${role.role}`}
              onChange={(_: ChangeEvent<HTMLSelectElement>, data: SelectOnChangeData) => {
                const next = { ...value }
                if (data.value) next[role.role] = data.value
                else delete next[role.role]
                onChange(next)
              }}
            >
              <option value="">{role.required ? 'Select a target' : 'Use the generator default'}</option>
              {unregistered && <option value={selected} disabled>{selected}</option>}
              {targets.map((target) => (
                <option key={target.target_registry_name} value={target.target_registry_name}>
                  {targetOptionLabel(target)}
                </option>
              ))}
            </Select>
          </Field>
        )
      })}
    </div>
  )
}
