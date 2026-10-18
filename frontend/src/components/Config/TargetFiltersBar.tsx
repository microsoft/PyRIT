import { Button, Tooltip } from '@fluentui/react-components'
import { FilterDismissRegular } from '@fluentui/react-icons'
import SearchableMultiCombobox from '@/components/SearchableMultiCombobox'
import {
  DEFAULT_TARGET_FILTERS,
  hasActiveTargetFilters,
  type TargetFilterOptions,
  type TargetFilters,
} from './targetFilters'
import { useTargetFiltersBarStyles } from './TargetFiltersBar.styles'

interface FilterField {
  key: keyof TargetFilters
  label: string
  /** Shown before the selection, because inputs and outputs offer the same choices. */
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
            disabledFocusable={!hasActiveTargetFilters(filters)}
            onClick={() => onFiltersChange({ ...DEFAULT_TARGET_FILTERS })}
            data-testid="target-reset-filters-btn"
          />
        </Tooltip>
      </div>
      <div className={styles.filters}>
        {shownFields.map((field: FilterField) => (
          <SearchableMultiCombobox
            key={field.key}
            className={styles.filterDropdown}
            ariaLabel={field.label}
            placeholder={field.placeholder}
            summaryPrefix={field.name}
            options={options[field.key]}
            selectedOptions={filters[field.key]}
            onSelect={(selected: string[]) => onFiltersChange({ ...filters, [field.key]: selected })}
            testId={field.testId}
          />
        ))}
      </div>
    </div>
  )
}
