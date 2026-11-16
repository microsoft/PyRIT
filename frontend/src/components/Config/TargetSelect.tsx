import type { ChangeEvent } from 'react'

import { Select, type SelectOnChangeData } from '@fluentui/react-components'

import HelpField from '@/components/HelpField'
import type { TargetInstance } from '@/types'
import { targetAccountLabelSuffix, targetNeedsCallerKey } from '@/utils/targetAccount'
import { targetModelName } from '@/utils/targetIdentity'

import { useTargetSelectStyles } from './TargetSelect.styles'

interface TargetSelectProps {
  targets: TargetInstance[]
  value: string
  onChange: (target: TargetInstance | null) => void
  label: string
  hint?: string
  readonly help?: string
  placeholder?: string
  disabled?: boolean
}

/**
 * A controlled registry selector; the caller owns selection and persistence.
 * Targets that need the user's own key stay listed but can't be chosen.
 */
export default function TargetSelect({
  targets,
  value,
  onChange,
  label,
  hint,
  help,
  placeholder = 'Select a target',
  disabled = false,
}: TargetSelectProps) {
  const styles = useTargetSelectStyles()
  return (
    <HelpField label={label} hint={hint} help={help}>
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
          const name = model ? `${target.target_registry_name} (${model})` : target.target_registry_name
          return (
            <option
              key={target.target_registry_name}
              value={target.target_registry_name}
              disabled={targetNeedsCallerKey(target)}
            >
              {`${name}${targetAccountLabelSuffix(target)}`}
            </option>
          )
        })}
      </Select>
    </HelpField>
  )
}
