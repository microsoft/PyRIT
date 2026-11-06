import { useRef, useState, type ChangeEvent, type KeyboardEvent } from 'react'

import {
  Button, Input, Popover, PopoverSurface, PopoverTrigger, Tooltip, mergeClasses,
} from '@fluentui/react-components'
import type { InputOnChangeData } from '@fluentui/react-components'
import { CheckmarkRegular, ChevronDownRegular, DismissRegular, TemperatureRegular } from '@fluentui/react-icons'

import HelpField from '@/components/HelpField'
import type { RequestGenerationDraft, TargetInstance } from '@/types'
import { generationFieldError, generationFieldRange } from '@/utils/requestGeneration'

import { useGenerationSettingsStyles } from './GenerationSettings.styles'

interface GenerationSettingsProps {
  readonly value: RequestGenerationDraft
  readonly onChange: (value: RequestGenerationDraft) => void
  readonly target?: TargetInstance | null
  readonly disabled: boolean
}

interface TemperatureEdit {
  readonly original: string | undefined
  readonly current: RequestGenerationDraft
}

export default function GenerationSettings(props: GenerationSettingsProps) {
  const { target, disabled } = props
  return <TemperatureControl key={JSON.stringify([
    target?.target_registry_name, target?.identifier.hash, target?.capabilities?.supports_temperature,
    target?.capabilities?.temperature_range, disabled,
  ])} {...props} />
}

function TemperatureControl({ value, onChange, target, disabled }: GenerationSettingsProps) {
  const styles = useGenerationSettingsStyles()
  const triggerRef = useRef<HTMLButtonElement>(null)
  const [edit, setEdit] = useState<TemperatureEdit | null>(null)
  const input = value.temperature
  const hasOverride = input !== undefined
  const supported = Boolean(target?.capabilities?.supports_temperature)
  const editable = supported && !disabled
  const error = generationFieldError('temperature', input, target?.capabilities)
  const [minimum, maximum] = generationFieldRange('temperature', target?.capabilities)
  const defaultValue = target?.binding?.temperature ?? target?.identifier.temperature
  const reason = !target ? 'Select a target to adjust temperature.'
    : !supported ? 'This target does not support runtime temperature.'
      : disabled ? 'Temperature cannot be changed while the prompt is disabled.' : undefined
  const description = reason ?? error ?? (hasOverride
    ? `Temperature override: ${input}.` : `Using target default: ${defaultValue ?? 'not specified; provider default unknown'}.`)
  const label = supported ? `Adjust temperature: ${hasOverride ? input?.trim() || 'invalid value' : 'target default'}`
    : 'Temperature unavailable'

  // An externally restored or reset draft must not inherit an older edit's cancel value.
  if (edit && edit.current !== value) setEdit(null)

  const changeTemperature = (temperature: string | undefined): void => {
    const next = { ...value }
    if (temperature === undefined) delete next.temperature
    else next.temperature = temperature
    setEdit((current: TemperatureEdit | null) => current && { ...current, current: next })
    onChange(next)
  }
  const close = (): void => {
    setEdit(null)
    triggerRef.current?.focus()
  }
  const cancel = (): void => {
    if (edit) changeTemperature(edit.original)
    close()
  }

  return <div className={styles.root} role="group" aria-label="Request generation settings">
    <Popover positioning="above-end" unstable_disableAutoFocus open={edit !== null && editable}
      onOpenChange={(event: { type: string; key?: string }, data: { open: boolean }) => {
        if (data.open && editable) setEdit({ original: input, current: value })
        else if (event.type === 'keydown' && 'key' in event && event.key === 'Escape') cancel()
        else setEdit(null)
      }}>
      <Tooltip content={editable ? `Adjust temperature. ${description}` : description} relationship="description">
        <PopoverTrigger disableButtonEnhancement>
          <Button ref={triggerRef} size="small" appearance={editable ? 'secondary' : 'subtle'}
            icon={<TemperatureRegular />} disabled={disabled} disabledFocusable={!disabled && !editable}
            className={mergeClasses(styles.button, supported && hasOverride && styles.override, supported && !!error && styles.invalid)}
            aria-label={label} aria-invalid={!!error} data-testid="temperature-control">
            {supported && hasOverride && <span className={styles.value}>{input.trim() || '!'}</span>}
            {editable && <ChevronDownRegular className={styles.chevron} aria-hidden="true" data-testid="temperature-chevron" />}
          </Button>
        </PopoverTrigger>
      </Tooltip>
      <PopoverSurface className={styles.surface} role="dialog" aria-label="Temperature">
        <div className={styles.content} onKeyDown={(event: KeyboardEvent<HTMLDivElement>) => {
          if (event.key === 'Escape') {
            event.stopPropagation()
            cancel()
          }
        }}>
          <HelpField label="Temperature" validationState={error ? 'error' : 'none'} validationMessage={error}
            hint="Blank uses target default."
            help={`Enter ${minimum} through ${maximum}. Configured default: ${defaultValue ?? 'not specified; provider default unknown'}. Applies to subsequent sends until changed; target defaults and earlier messages are unchanged.`}>
            <Input autoFocus type="text" inputMode="decimal" value={input ?? ''} placeholder="Default" disabled={!editable}
              onChange={(_event: ChangeEvent<HTMLInputElement>, data: InputOnChangeData) => {
                const next = data.value.trim() ? data.value : undefined
                changeTemperature(next)
              }}
              onKeyDown={(event: KeyboardEvent<HTMLInputElement>) => {
                if (event.key === 'Enter') {
                  event.preventDefault()
                  event.stopPropagation()
                  if (!error) close()
                }
              }} />
          </HelpField>
          <div className={styles.actions}>
            <Button appearance="subtle" size="small" className={styles.button}
              onClick={() => { changeTemperature(undefined); close() }}>Use target default</Button>
            <Button appearance="subtle" icon={<CheckmarkRegular />} className={styles.button}
              aria-label="Apply temperature" disabled={!!error} onClick={close} />
            <Button appearance="subtle" icon={<DismissRegular />} className={styles.button}
              aria-label="Cancel temperature edit" onClick={cancel} />
          </div>
        </div>
      </PopoverSurface>
    </Popover>
    {!supported && hasOverride && <Tooltip content={`Clear retained temperature override: ${input || 'invalid value'}.`} relationship="description">
      <Button size="small" appearance="subtle" icon={<DismissRegular />} className={styles.button} disabled={disabled}
        aria-label="Clear temperature override" onClick={() => changeTemperature(undefined)} />
    </Tooltip>}
  </div>
}
