import type { ChangeEvent } from 'react'

import { Field, Select, Spinner, type SelectOnChangeData } from '@fluentui/react-components'

import type { PromptGeneratorTargetRole, TargetInstance } from '@/types'
import { targetModelName } from '@/utils/targetIdentity'

import type { TargetRoleFilter } from './generationFormHelpers'
import { usePromptGenerationStyles } from './PromptGeneration.styles'

interface GenerationTargetFieldsProps {
  readonly roles: readonly PromptGeneratorTargetRole[]
  readonly targets: readonly TargetInstance[]
  readonly loading: boolean
  readonly error: string | null
  readonly value: Readonly<Record<string, string>>
  readonly onChange: (value: Record<string, string>) => void
  readonly filters?: Readonly<Record<string, TargetRoleFilter>>
  readonly disabled?: boolean
}

function targetLabel(target: TargetInstance): string {
  const model = targetModelName(target)
  return model ? `${target.target_registry_name} (${model})` : target.target_registry_name
}

/** One target picker per generator role, chosen from the target registry. */
export default function GenerationTargetFields({
  roles,
  targets,
  loading,
  error,
  value,
  onChange,
  filters = {},
  disabled = false,
}: GenerationTargetFieldsProps) {
  const styles = usePromptGenerationStyles()
  if (loading) return <Spinner size="tiny" label="Loading targets..." />

  return (
    <div className={styles.fieldGrid}>
      {roles.map((role) => {
        const filter = filters[role.role]
        const selected = value[role.role] ?? ''
        const offered = filter ? targets.filter((target) => filter.allows(target)) : [...targets]
        const selectedTarget = targets.find((target) => target.target_registry_name === selected)
        const selectedRejected = selected !== '' && !offered.some((target) => target.target_registry_name === selected)
        const validation = error && targets.length === 0
          ? error
          : selectedRejected
            ? (selectedTarget && filter
              ? `"${selected}" can't be used for this role. ${filter.reason}`
              : `"${selected}" is not a registered target.`)
            : undefined
        const hint = [role.description, filter?.reason].filter(Boolean).join(' ')
        return (
          <Field
            key={role.role}
            label={role.label}
            required={role.required}
            hint={hint}
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
              {selectedRejected && <option value={selected} disabled>{selected}</option>}
              {offered.map((target) => (
                <option key={target.target_registry_name} value={target.target_registry_name}>
                  {targetLabel(target)}
                </option>
              ))}
            </Select>
          </Field>
        )
      })}
    </div>
  )
}
