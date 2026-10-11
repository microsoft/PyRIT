import { useId, useState } from 'react'

import { Button, Field, Select, Text } from '@fluentui/react-components'

import type { RegistryReferenceOption } from '@/types'

import { useReferenceFieldStyles } from './ReferenceField.styles'

interface ReferenceFieldProps {
  label: string
  options: RegistryReferenceOption[]
  value: string | string[]
  multiple?: boolean
  disabled?: boolean
  hint?: string
  onChange: (value: string | string[]) => void
}

/** Ordered registry references. Repeated entries are intentional. */
export default function ReferenceField({
  label, options, value, multiple = false, disabled = false, hint, onChange,
}: ReferenceFieldProps) {
  const styles = useReferenceFieldStyles()
  const [pending, setPending] = useState('')
  const [entryIds, setEntryIds] = useState<string[]>([])
  const id = useId()
  const [nextId, setNextId] = useState(0)
  const selected = Array.isArray(value) ? value : []

  const move = (index: number, delta: number): void => {
    const next = [...selected]
    const ids = selected.map((_, position) => entryIds[position] ?? `${id}-initial-${position}`)
    ;[next[index], next[index + delta]] = [next[index + delta], next[index]]
    ;[ids[index], ids[index + delta]] = [ids[index + delta], ids[index]]
    setEntryIds(ids)
    onChange(next)
  }

  return (
    <div className={styles.root}>
      <Field label={label} hint={hint}>
        <Select
          value={multiple ? pending : typeof value === 'string' ? value : ''}
          disabled={disabled}
          onChange={(_, data) => multiple ? setPending(data.value) : onChange(data.value)}
        >
          <option value="">{multiple ? 'Select an instance to add' : 'Use default / not set'}</option>
          {options.map((option) => (
            <option key={option.name} value={option.name}>{option.name} ({option.type})</option>
          ))}
        </Select>
      </Field>
      {multiple && (
        <>
          <Button
            className={styles.action}
            disabled={disabled || !pending}
            onClick={() => {
              setEntryIds([...selected.map((_, index) => entryIds[index] ?? `${id}-initial-${index}`), `${id}-${nextId}`])
              setNextId(nextId + 1)
              onChange([...selected, pending])
            }}
          >Add to {label}</Button>
          {selected.map((name, index) => (
            <div className={styles.row} key={entryIds[index] ?? `${id}-initial-${index}`}>
              <Text>{index + 1}. {name}</Text>
              <Button className={styles.action} aria-label={`Move ${label} ${index + 1} up`}
                disabled={disabled || index === 0} onClick={() => move(index, -1)}>Up</Button>
              <Button className={styles.action} aria-label={`Move ${label} ${index + 1} down`}
                disabled={disabled || index === selected.length - 1} onClick={() => move(index, 1)}>Down</Button>
              <Button className={styles.action} aria-label={`Remove ${label} ${index + 1}`} disabled={disabled}
                onClick={() => {
                  setEntryIds(selected.map((_, position) => entryIds[position] ?? `${id}-initial-${position}`)
                    .filter((_, position) => position !== index))
                  onChange(selected.filter((_, position) => position !== index))
                }}>Remove</Button>
            </div>
          ))}
        </>
      )}
    </div>
  )
}
