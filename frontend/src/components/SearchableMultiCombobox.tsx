import { useState } from 'react'
import { Combobox, Option } from '@fluentui/react-components'
import type { FilterOption } from '@/types'
import { useSearchableMultiComboboxStyles } from './SearchableMultiCombobox.styles'

interface SearchableMultiComboboxProps {
  options: FilterOption[]
  selectedOptions: string[]
  onSelect: (selected: string[]) => void
  placeholder: string
  testId: string
  className?: string
  ariaLabel?: string
  /** Names the filter in the closed text, e.g. "Inputs" gives "Inputs: Image (+1)". */
  summaryPrefix?: string
}

/** The first selected choice and how many more, e.g. "alice (+2)". */
function formatSummary(selected: string[], options: FilterOption[], prefix?: string): string {
  if (selected.length === 0) return ''
  const first = options.find((option: FilterOption) => option.value === selected[0])?.label ?? selected[0]
  const summary = selected.length === 1 ? first : `${first} (+${selected.length - 1})`
  return prefix ? `${prefix}: ${summary}` : summary
}

/**
 * Multi-select Combobox that summarizes the selection while closed and lets you type to narrow the choices while open.
 */
export default function SearchableMultiCombobox({
  options,
  selectedOptions,
  onSelect,
  placeholder,
  testId,
  className,
  ariaLabel,
  summaryPrefix,
}: SearchableMultiComboboxProps) {
  const styles = useSearchableMultiComboboxStyles()
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const query = search.trim().toLowerCase()
  const shownOptions = query
    ? options.filter((option: FilterOption) => option.label.toLowerCase().includes(query))
    : options

  // Fluent's multiselect Combobox does not show the selection in its input, so the value is driven here: the
  // summary while closed, the typed search while open.
  return (
    <Combobox
      className={className}
      aria-label={ariaLabel}
      placeholder={placeholder}
      multiselect
      freeform
      open={open}
      onOpenChange={(_event, data) => {
        setOpen(data.open)
        setSearch('')
      }}
      selectedOptions={selectedOptions}
      value={open ? search : formatSummary(selectedOptions, options, summaryPrefix)}
      onChange={(event) => setSearch(event.target.value)}
      onOptionSelect={(_event, data) => {
        onSelect(data.selectedOptions)
        setSearch('')
      }}
      data-testid={testId}
    >
      {shownOptions.map((option: FilterOption) => (
        <Option key={option.value} className={styles.option} value={option.value} text={option.label}>
          {option.label}
        </Option>
      ))}
    </Combobox>
  )
}
