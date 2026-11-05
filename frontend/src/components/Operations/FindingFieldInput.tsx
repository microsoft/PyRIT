import { useState, type JSX } from 'react'
import { Combobox, Field, Input, Option, Textarea } from '@fluentui/react-components'

import type { FindingField, FindingFieldOption } from '@/types'
import { choiceLevels, filled, findOption, levelOptions, ownValue } from '@/utils/findingFields'

import { useFindingFieldInputStyles } from './FindingFieldInput.styles'

// Option keys are never blank (the backend rejects them), so a whitespace-only value can't be one. '' is Not set.
const OTHER_VALUE = ' '
const OTHER_TEXT = 'Other'
const NOT_SET_TEXT = 'Not set'
const NO_MATCH_TEXT = 'No matching options'
const UNMATCHED_HINT = 'Not a current option. It stays unless you change it.'

interface FindingFieldInputProps {
  field: FindingField
  /** The whole draft by key. The severity slot's value goes under `severity`. */
  values: Record<string, string>
  disabled?: boolean
  /** Reports the keys to set. '' clears a value. */
  onChange: (changes: Record<string, string>) => void
  /** Reports whether the Other text box is showing with blank text. */
  onOtherBlankChange?: (blank: boolean) => void
}

interface FieldControlProps {
  field: FindingField
  values: Record<string, string>
  disabled: boolean
  onChange: (changes: Record<string, string>) => void
}

interface ChoiceFieldInputProps extends FieldControlProps {
  onOtherBlankChange?: (blank: boolean) => void
}

interface PickerOption {
  value: string
  text: string
}

interface LevelState {
  options: PickerOption[]
  selected: string
  hint?: string
  /** Required only where the path offers options, as the backend checks a required cascade. */
  required: boolean
  unavailable: boolean
}

/**
 * What one dropdown of a choice field offers and shows. A stored value that isn't an option under its path is
 * listed as typed and stays selected, unless the field is showing Other text.
 */
function levelState(field: FindingField, values: Record<string, string>, depth: number, otherChosen: boolean): LevelState {
  const levels = choiceLevels(field)
  const fieldOptions = levelOptions(field, values, depth)
  const options = fieldOptions.map((option: FindingFieldOption) => ({ value: option.key, text: option.label }))
  const value = filled(ownValue(values, levels[depth].key)) ?? ''
  const unmatched = !otherChosen && value !== '' && findOption(fieldOptions, value) === undefined
  const parentBlank = depth > 0 && filled(ownValue(values, levels[depth - 1].key)) === null
  return {
    options: unmatched ? [{ value, text: value }, ...options] : options,
    selected: otherChosen ? OTHER_VALUE : value,
    hint: unmatched ? UNMATCHED_HINT : depth === 0 ? field.hint ?? undefined : undefined,
    required: field.required && fieldOptions.length > 0,
    unavailable: depth > 0 && (parentBlank || (options.length === 0 && !unmatched)),
  }
}

/** The chosen value, plus a clear for each lower level whose value isn't an option under the new path. */
function cascadeChanges(
  field: FindingField,
  values: Record<string, string>,
  depth: number,
  value: string,
): Record<string, string> {
  const levels = choiceLevels(field)
  const changes: Record<string, string> = { [levels[depth].key]: value }
  const next = { ...values, ...changes }
  for (const [lower, level] of levels.entries()) {
    if (lower <= depth) continue
    const current = filled(ownValue(next, level.key))
    if (current === null || findOption(levelOptions(field, next, lower), current) !== undefined) continue
    changes[level.key] = ''
    next[level.key] = ''
  }
  return changes
}

function selectedText(options: PickerOption[], selected: string): string {
  if (selected === OTHER_VALUE) return OTHER_TEXT
  return options.find((option: PickerOption) => option.value === selected)?.text ?? ''
}

interface ChoiceLevelPickerProps {
  label: string
  hint?: string
  required: boolean
  disabled: boolean
  /** The options the typed text filters. Not set and Other always show. */
  options: PickerOption[]
  selected: string
  offerNotSet: boolean
  offerOther: boolean
  onSelect: (optionValue: string) => void
}

function ChoiceLevelPicker({
  label, hint, required, disabled, options, selected, offerNotSet, offerOther, onSelect,
}: ChoiceLevelPickerProps): JSX.Element {
  const styles = useFindingFieldInputStyles()
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const matches = options.filter((option: PickerOption) => option.text.toLowerCase().includes(search.toLowerCase()))

  return (
    <Field label={label} hint={hint} required={required}>
      <Combobox className={styles.picker} inlinePopup open={open} disabled={disabled}
        placeholder={offerNotSet ? NOT_SET_TEXT : undefined}
        value={open ? search : selectedText(options, selected)} selectedOptions={[selected]}
        onOpenChange={(_, data) => { setOpen(data.open); setSearch('') }}
        onChange={(event) => { setSearch(event.target.value); setOpen(true) }}
        onOptionSelect={(_, data) => {
          // Typed text that no longer matches the selection clears Fluent's selection without a value. Clicking the
          // selected option, or blurring on its text, selects it again, so only a different option is a change.
          if (data.optionValue === undefined) return
          setOpen(false)
          setSearch('')
          if (data.optionValue !== selected) onSelect(data.optionValue)
        }}
        listbox={{ className: styles.listbox }}>
        {offerNotSet && <Option value="">{NOT_SET_TEXT}</Option>}
        {matches.map((option: PickerOption) => <Option key={option.value} value={option.value}>{option.text}</Option>)}
        {matches.length === 0 && <Option disabled>{NO_MATCH_TEXT}</Option>}
        {offerOther && <Option value={OTHER_VALUE}>{OTHER_TEXT}</Option>}
      </Combobox>
    </Field>
  )
}

function ChoiceFieldInput({
  field, values, disabled, onChange, onOtherBlankChange,
}: ChoiceFieldInputProps): JSX.Element {
  const styles = useFindingFieldInputStyles()
  const levels = choiceLevels(field)
  const allowOther = field.allow_other && levels.length === 1
  const [otherChosen, setOtherChosen] = useState(() => {
    const value = filled(ownValue(values, field.key))
    return allowOther && value !== null && findOption(field.options, value) === undefined
  })

  const select = (depth: number, optionValue: string): void => {
    if (allowOther) {
      setOtherChosen(optionValue === OTHER_VALUE)
      onOtherBlankChange?.(optionValue === OTHER_VALUE)
    }
    onChange(cascadeChanges(field, values, depth, optionValue === OTHER_VALUE ? '' : optionValue))
  }

  return (
    <div className={styles.levels}>
      {levels.map((level: { key: string; label: string }, depth: number) => {
        const state = levelState(field, values, depth, depth === 0 && otherChosen)
        return (
          <ChoiceLevelPicker key={level.key} label={level.label} hint={state.hint} required={state.required}
            disabled={disabled || state.unavailable} options={state.options} selected={state.selected}
            offerNotSet={depth === 0 && !field.required} offerOther={allowOther}
            onSelect={(optionValue: string) => { select(depth, optionValue) }} />
        )
      })}
      {otherChosen && <Field label={`${OTHER_TEXT} ${field.label}`} required>
        <Input value={ownValue(values, field.key) ?? ''} disabled={disabled} name={field.key} autoComplete="off"
          onChange={(_, data) => {
            onOtherBlankChange?.(data.value.trim() === '')
            onChange({ [field.key]: data.value })
          }} />
      </Field>}
    </div>
  )
}

function TextFieldInput({ field, values, disabled, onChange }: FieldControlProps): JSX.Element {
  const value = ownValue(values, field.key) ?? ''
  const report = (text: string): void => { onChange({ [field.key]: text }) }
  return (
    <Field label={field.label} hint={field.hint ?? undefined} required={field.required}>
      {field.type === 'multiline'
        ? <Textarea value={value} disabled={disabled} resize="vertical" name={field.key} autoComplete="off"
          onChange={(_, data) => { report(data.value) }} />
        : <Input value={value} disabled={disabled} name={field.key} autoComplete="off"
          onChange={(_, data) => { report(data.value) }} />}
    </Field>
  )
}

/**
 * The input for one finding field: a text box for a `text` or `multiline` field, and one searchable dropdown per
 * level for a `choice` field. A single-level choice with `allow_other` also takes typed Other text.
 */
export default function FindingFieldInput({
  field, values, disabled = false, onChange, onOtherBlankChange,
}: FindingFieldInputProps): JSX.Element {
  if (field.type === 'choice') {
    return <ChoiceFieldInput field={field} values={values} disabled={disabled} onChange={onChange}
      onOtherBlankChange={onOtherBlankChange} />
  }
  return <TextFieldInput field={field} values={values} disabled={disabled} onChange={onChange} />
}
