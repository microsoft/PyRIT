import { useState } from 'react'

import { Button, Checkbox, Field, Input, Popover, PopoverSurface, PopoverTrigger, Text } from '@fluentui/react-components'

import type { ScenarioDatasetLimit } from '@/types'
import { useScenarioDatasetTableStyles } from './ScenarioDatasetTable.styles'
import { parseCaseLimit } from './datasetPresentation'

interface DatasetLimitControlProps {
  label: string
  value: ScenarioDatasetLimit | null | undefined
  defaultValue: number | 'all'
  text?: string
  showLabel?: boolean
  hint?: string
  draft?: string
  disabled: boolean
  onChange: (value: ScenarioDatasetLimit) => void
  onDraftChange: (text: string | null) => void
}

export default function DatasetLimitControl({
  label, value, defaultValue, text, showLabel = true, hint, draft, disabled, onChange, onDraftChange,
}: DatasetLimitControlProps) {
  const styles = useScenarioDatasetTableStyles()
  const [open, setOpen] = useState(false)
  const limit = typeof value === 'number' || value === 'all' ? value : defaultValue
  const invalid = draft !== undefined && parseCaseLimit(draft) === null
  const control = <Popover open={open} onOpenChange={(_, data) => setOpen(data.open)}>
    <PopoverTrigger disableButtonEnhancement>
      <Button appearance="transparent" className={styles.limitButton} disabled={disabled}
        aria-label={`Edit ${label}`}>{text ?? (limit === 'all' ? 'All available' : `Up to ${limit.toLocaleString()}`)}</Button>
    </PopoverTrigger>
    <PopoverSurface aria-label={`Edit ${label}`}>
      <div className={styles.limitEditor}>
        <Field label={label} validationState={invalid ? 'error' : 'none'}
          validationMessage={invalid ? 'Enter a positive integer.' : undefined}>
          <Input aria-label={label} inputMode="numeric" disabled={disabled || limit === 'all'}
            value={draft ?? (limit === 'all' ? '' : String(limit))}
            onChange={(_, data) => {
              onDraftChange(data.value)
              const parsed = parseCaseLimit(data.value)
              if (parsed !== null) onChange(parsed)
            }} />
        </Field>
        <Checkbox label="Use all available" aria-label={`All available for ${label}`} checked={limit === 'all'} disabled={disabled}
          onChange={(_, data) => {
            onDraftChange(null)
            onChange(data.checked ? 'all' : typeof defaultValue === 'number' ? defaultValue : 1)
          }} />
        {hint && <Text size={200}>{hint}</Text>}
        <div className={styles.controls}>
          <Button className={styles.button} disabled={disabled} onClick={() => {
            onDraftChange(null)
            onChange('default')
          }}>Reset to {defaultValue === 'all' ? 'all available' : defaultValue}</Button>
          <Button className={styles.button} disabled={disabled || invalid} onClick={() => setOpen(false)}>Done</Button>
        </div>
      </div>
    </PopoverSurface>
  </Popover>
  return showLabel ? <div className={styles.totalLimit}><Text>{label}:</Text>{control}</div> : control
}
