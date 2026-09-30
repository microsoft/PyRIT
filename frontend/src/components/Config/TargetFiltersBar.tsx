import { useState } from 'react'
import { Button, Combobox, Option, Tooltip } from '@fluentui/react-components'
import { FilterDismissRegular } from '@fluentui/react-icons'
import {
  DEFAULT_TARGET_FILTERS,
  hasActiveTargetFilters,
  type FilterOption,
  type TargetFilterOptions,
  type TargetFilters,
} from './targetFilters'
import { useTargetFiltersBarStyles } from './TargetFiltersBar.styles'

interface FilterField {
  key: keyof TargetFilters
  label: string
  name: string
  placeholder: string
  testId: string
}

/** The filters in display order. */
const FILTER_FIELDS: readonly FilterField[] = [
  { key: 'types', label: 'Filter by type:', name: 'Type', placeholder: 'All types', testId: 'target-type-filter' },
  { key: 'inputs', label: 'Filter by input:', name: 'Inputs', placeholder: 'All inputs', testId: 'target-input-filter' },
  { key: 'outputs', label: 'Filter by output:', name: 'Outputs', placeholder: 'All outputs', testId: 'target-output-filter' },
  { key: 'capabilities', label: 'Filter by capability:', name: 'Capabilities', placeholder: 'All capabilities', testId: 'target-capability-filter' },
]

/**
 * Name the filter, then show the first selected choice and how many more, like the History filters.
 * The name matters because inputs and outputs offer the same choices.
 */
function formatSelection(name: string, selected: string[], options: FilterOption[]): string {
  if (selected.length === 0) return ''
  const first = options.find((option: FilterOption) => option.value === selected[0])?.label ?? selected[0]
  return selected.length === 1 ? `${name}: ${first}` : `${name}: ${first} (+${selected.length - 1})`
}

interface FilterComboboxProps {
  field: FilterField
  selected: string[]
  options: FilterOption[]
  onSelect: (selected: string[]) => void
}

/** One multiselect filter. Like the History filters, typing narrows its choices while it is open. */
function FilterCombobox({ field, selected, options, onSelect }: FilterComboboxProps) {
  const styles = useTargetFiltersBarStyles()
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const query = search.trim().toLowerCase()
  const shownOptions = query
    ? options.filter((option: FilterOption) => option.label.toLowerCase().includes(query))
    : options
  return (
    <Combobox
      className={styles.filterDropdown}
      aria-label={field.label}
      placeholder={field.placeholder}
      multiselect
      freeform
      open={open}
      onOpenChange={(_event, data) => {
        setOpen(data.open)
        setSearch('')
      }}
      selectedOptions={selected}
      value={open ? search : formatSelection(field.name, selected, options)}
      onChange={(event) => setSearch(event.target.value)}
      onOptionSelect={(_event, data) => {
        onSelect(data.selectedOptions)
        setSearch('')
      }}
      data-testid={field.testId}
    >
      {shownOptions.map((option: FilterOption) => (
        <Option key={option.value} className={styles.option} value={option.value} text={option.label}>
          {option.label}
        </Option>
      ))}
    </Combobox>
  )
}

interface TargetFiltersBarProps {
  filters: TargetFilters
  options: TargetFilterOptions
  onFiltersChange: (filters: TargetFilters) => void
}

/** Multi-select filters for the target table; renders nothing when no filter can narrow it. */
export default function TargetFiltersBar({ filters, options, onFiltersChange }: TargetFiltersBarProps) {
  const styles = useTargetFiltersBarStyles()
  const shownFields = FILTER_FIELDS.filter(({ key }) => options[key].length > 0)
  if (shownFields.length === 0) {
    return null
  }
  return (
    <div className={styles.root} data-testid="target-filters">
      <div className={styles.resetSlot}>
        <Tooltip content="Reset all filters" relationship="label">
          <Button
            className={styles.resetButton}
            appearance="subtle"
            size="small"
            icon={<FilterDismissRegular />}
            aria-label="Reset all filters"
            disabled={!hasActiveTargetFilters(filters)}
            onClick={() => onFiltersChange({ ...DEFAULT_TARGET_FILTERS })}
            data-testid="target-reset-filters-btn"
          />
        </Tooltip>
      </div>
      <div className={styles.filters}>
        {shownFields.map((field: FilterField) => (
          <FilterCombobox
            key={field.key}
            field={field}
            selected={filters[field.key]}
            options={options[field.key]}
            onSelect={(selected: string[]) => onFiltersChange({ ...filters, [field.key]: selected })}
          />
        ))}
      </div>
    </div>
  )
}
