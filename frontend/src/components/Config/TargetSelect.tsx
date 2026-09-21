import type { ChangeEvent } from 'react'

import { Field, Select, type SelectOnChangeData } from '@fluentui/react-components'

import type { TargetInstance } from '@/types'
import { targetModelName } from '@/utils/targetIdentity'

import { useTargetSelectStyles } from './TargetSelect.styles'

interface TargetSelectProps {
  targets: TargetInstance[]
  value: string
  onChange: (target: TargetInstance | null) => void
  label: string
  hint?: string
  placeholder?: string
  disabled?: boolean
}

/** A controlled registry selector; the caller owns selection and persistence. */
export default function TargetSelect({
  targets,
  value,
  onChange,
  label,
  hint,
  placeholder = 'Select a target',
  disabled = false,
}: TargetSelectProps) {
  const styles = useTargetSelectStyles()
  return (
    <Field label={label} hint={hint}>
      <Select
        className={styles.select}
        value={value}
        disabled={disabled}
        onChange={(_: ChangeEvent<HTMLSelectElement>, data: SelectOnChangeData) => {
          onChange(targets.find((target: TargetInstance) => target.target_registry_name === data.value) ?? null)
        }}
      >
        <option value="">{placeholder}</option>
        {targets.map((target: TargetInstance) => {
          const model = targetModelName(target)
          return (
            <option key={target.target_registry_name} value={target.target_registry_name}>
              {model ? `${target.target_registry_name} (${model})` : target.target_registry_name}
            </option>
          )
        })}
      </Select>
    </Field>
  )
}
