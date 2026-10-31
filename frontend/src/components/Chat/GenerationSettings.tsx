import { useState, type ChangeEvent } from 'react'

import {
  Button, Checkbox, Field, Input, Popover, PopoverSurface, PopoverTrigger, Text,
} from '@fluentui/react-components'
import type { CheckboxOnChangeData, InputOnChangeData } from '@fluentui/react-components'
import { OptionsRegular } from '@fluentui/react-icons'

import type { RequestGenerationDraft, TargetInstance } from '@/types'
import { GENERATION_FIELDS, generationFieldError } from '@/utils/requestGeneration'

import { useGenerationSettingsStyles } from './GenerationSettings.styles'

interface GenerationSettingsProps {
  readonly value: RequestGenerationDraft
  readonly onChange: (value: RequestGenerationDraft) => void
  readonly target?: TargetInstance | null
  readonly disabled: boolean
}

export default function GenerationSettings({ value, onChange, target, disabled }: GenerationSettingsProps) {
  const styles = useGenerationSettingsStyles()
  const [open, setOpen] = useState(false)
  const overrides = GENERATION_FIELDS.filter((field) => value[field.name] !== undefined)
  const summary = overrides.map((field) => `${field.label}: ${value[field.name]?.trim() || 'value required'}`).join('; ')
  const unavailable = disabled || !target

  return <div className={styles.root} role="group" aria-label="Request generation settings">
    <Popover positioning="above-start" open={open && !unavailable}
      onOpenChange={(_event: unknown, data: { open: boolean }) => setOpen(data.open)}>
      <PopoverTrigger disableButtonEnhancement>
        <Button size="small" appearance={overrides.length ? 'secondary' : 'subtle'} icon={<OptionsRegular />}
          className={styles.button} disabled={unavailable} aria-label="Generation settings">
          Generation settings
        </Button>
      </PopoverTrigger>
      <PopoverSurface className={styles.surface} role="dialog" aria-label="Generation settings">
        <div className={styles.content}>
          <Text weight="semibold">Generation settings</Text>
          <Text size={200}>Applies to this request and subsequent sends until changed. Earlier messages are unchanged.</Text>
          {GENERATION_FIELDS.map((field) => {
            const input = value[field.name]
            const useDefault = input === undefined
            const supported = Boolean(target?.capabilities?.[field.capability])
            const defaultValue = target?.binding?.[field.name] ?? target?.identifier[field.name]
            const error = generationFieldError(field.name, input, target?.capabilities)
            return <div className={styles.field} key={field.name}>
              <Checkbox checked={useDefault} disabled={disabled || !supported}
                label={`Use target default for ${field.label.toLowerCase()}`}
                onChange={(_event: ChangeEvent<HTMLInputElement>, data: CheckboxOnChangeData) => {
                  const next = { ...value }
                  if (data.checked) delete next[field.name]
                  else next[field.name] = ''
                  onChange(next)
                }} />
              <Field label={field.label} validationState={error ? 'error' : 'none'} validationMessage={error}
                hint={!supported ? `This target does not support runtime ${field.name}.`
                  : useDefault ? `No request override. Configured default: ${defaultValue ?? 'not specified'}. Existing target/provider settings apply.`
                    : `Enter ${field.min} through ${field.max}. Zero is valid.`}>
                <Input type="number" min={field.min} max={field.max} step={field.step}
                  value={input ?? ''} disabled={disabled || !supported || useDefault}
                  placeholder={String(defaultValue ?? 'Default')}
                  onChange={(_event: ChangeEvent<HTMLInputElement>, data: InputOnChangeData) => onChange({ ...value, [field.name]: data.value })} />
              </Field>
            </div>
          })}
        </div>
      </PopoverSurface>
    </Popover>
    {summary && <Text size={200} className={styles.summary} aria-live="polite">{summary}</Text>}
  </div>
}
