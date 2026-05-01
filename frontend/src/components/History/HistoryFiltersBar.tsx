import { useState } from 'react'
import {
  Button,
  Tooltip,
  Option,
  OptionGroup,
  Combobox,
  Switch,
  mergeClasses,
} from '@fluentui/react-components'
import {
  FilterRegular,
  FilterDismissRegular,
} from '@fluentui/react-icons'
import { DEFAULT_HISTORY_FILTERS } from './historyFilters'
import type { HistoryFilters } from './historyFilters'
import { useAttackHistoryStyles } from './AttackHistory.styles'

const NO_CONVERTERS_SENTINEL = '__no_converters__'

const OUTCOME_LABELS: Record<string, string> = {
  success: 'Success',
  failure: 'Failure',
  undetermined: 'Undetermined',
}

// Fluent's multiselect Combobox doesn't auto-populate its input from selectedOptions;
// we have to drive the displayed text via `value` ourselves. This format is only
// shown when the popover is closed — while it's open we show the user's typed
// search text so typing-to-search actually works.
function formatMultiSelectValue(selected: string[]): string {
  if (selected.length === 0) return ''
  if (selected.length === 1) return selected[0]
  return `${selected[0]} (+${selected.length - 1})`
}

interface SearchableMultiComboboxProps {
  placeholder: string
  selectedOptions: string[]
  options: string[]
  onSelect: (selected: string[]) => void
  testid: string
  className?: string
}

function SearchableMultiCombobox({
  placeholder,
  selectedOptions,
  options,
  onSelect,
  testid,
  className,
}: SearchableMultiComboboxProps) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')

  const filtered = search
    ? options.filter((o) => o.toLowerCase().includes(search.toLowerCase()))
    : options

  return (
    <Combobox
      className={className}
      placeholder={placeholder}
      multiselect
      freeform
      open={open}
      onOpenChange={(_e, data) => {
        setOpen(data.open)
        // Clear search text both when opening (start fresh) and when closing
        // (so the formatted value is shown again).
        setSearch('')
      }}
      selectedOptions={selectedOptions}
      value={open ? search : formatMultiSelectValue(selectedOptions)}
      onChange={(e) => setSearch((e.target as HTMLInputElement).value)}
      onOptionSelect={(_e, data) => {
        onSelect(data.selectedOptions)
        setSearch('')
      }}
      data-testid={testid}
    >
      {filtered.map((o) => (
        <Option key={o} value={o}>{o}</Option>
      ))}
    </Combobox>
  )
}

interface HistoryFiltersBarProps {
  filters: HistoryFilters
  onFiltersChange: (filters: HistoryFilters) => void
  attackTypeOptions: string[]
  converterOptions: string[]
  operatorOptions: string[]
  operationOptions: string[]
  otherLabelOptions: string[]
}

export default function HistoryFiltersBar({
  filters,
  onFiltersChange,
  attackTypeOptions,
  converterOptions,
  operatorOptions,
  operationOptions,
  otherLabelOptions,
}: HistoryFiltersBarProps) {
  const styles = useAttackHistoryStyles()

  const {
    attackTypes: attackTypeFilters,
    outcome: outcomeFilter,
    converter: converterFilter,
    converterMatchMode,
    hasConverters,
    operator: operatorFilters,
    operation: operationFilters,
    otherLabels: otherLabelFilters,
    labelSearchText,
  } = filters

  const setFilter = <K extends keyof HistoryFilters>(key: K, value: HistoryFilters[K]) => {
    onFiltersChange({ ...filters, [key]: value })
  }

  const hasActiveFilters =
    attackTypeFilters.length > 0 ||
    outcomeFilter ||
    converterFilter.length > 0 ||
    hasConverters !== undefined ||
    operatorFilters.length > 0 ||
    operationFilters.length > 0 ||
    otherLabelFilters.length > 0

  // Converter Combobox selectedOptions includes the sentinel when hasConverters=false.
  const converterSelectedOptions = hasConverters === false
    ? [NO_CONVERTERS_SENTINEL]
    : converterFilter

  const handleConverterSelect = (selected: string[]) => {
    const hasSentinel = selected.includes(NO_CONVERTERS_SENTINEL)
    const realConverters = selected.filter((s) => s !== NO_CONVERTERS_SENTINEL)
    const sentinelWasActive = hasConverters === false
    const sentinelJustAdded = hasSentinel && !sentinelWasActive

    if (sentinelJustAdded || (hasSentinel && realConverters.length === 0)) {
      // User just toggled the sentinel on (or it's alone) → clear real converters
      onFiltersChange({ ...filters, converter: [], hasConverters: false })
    } else {
      // Any real converter toggled (sentinel was active or not) → drop sentinel
      onFiltersChange({ ...filters, converter: realConverters, hasConverters: undefined })
    }
  }

  const showMatchModeToggle = converterFilter.length >= 2 && hasConverters !== false

  // Searchable state for the converter Combobox (custom because of the
  // "(No converters)" sentinel OptionGroup).
  const [converterOpen, setConverterOpen] = useState(false)
  const [converterSearch, setConverterSearch] = useState('')
  const converterMatchesSearch = (c: string) =>
    !converterSearch || c.toLowerCase().includes(converterSearch.toLowerCase())
  const filteredConverterOptions = converterOptions.filter(converterMatchesSearch)

  return (
    <div className={styles.filters}>
      <FilterRegular />
      {hasActiveFilters && (
        <Tooltip content="Reset all filters" relationship="label">
          <Button
            appearance="subtle"
            size="small"
            icon={<FilterDismissRegular />}
            onClick={() => onFiltersChange({ ...DEFAULT_HISTORY_FILTERS })}
            data-testid="reset-filters-btn"
          >
            Reset
          </Button>
        </Tooltip>
      )}
      <SearchableMultiCombobox
        className={styles.filterDropdown}
        placeholder="All attack types"
        selectedOptions={attackTypeFilters}
        options={attackTypeOptions}
        onSelect={(selected) => setFilter('attackTypes', selected)}
        testid="attack-type-filter"
      />
      <Combobox
        className={styles.filterDropdown}
        placeholder="All outcomes"
        value={OUTCOME_LABELS[outcomeFilter] ?? ''}
        selectedOptions={outcomeFilter ? [outcomeFilter] : []}
        onOptionSelect={(_e, data) =>
          setFilter('outcome', data.selectedOptions[0] ?? '')
        }
        data-testid="outcome-filter"
      >
        <Option value="">All outcomes</Option>
        <Option value="success">Success</Option>
        <Option value="failure">Failure</Option>
        <Option value="undetermined">Undetermined</Option>
      </Combobox>
      <Combobox
        className={styles.filterDropdown}
        placeholder="All converters"
        multiselect
        freeform
        open={converterOpen}
        onOpenChange={(_e, data) => {
          setConverterOpen(data.open)
          setConverterSearch('')
        }}
        selectedOptions={converterSelectedOptions}
        value={
          converterOpen
            ? converterSearch
            : hasConverters === false
              ? '(No converters)'
              : formatMultiSelectValue(converterFilter)
        }
        onChange={(e) => setConverterSearch((e.target as HTMLInputElement).value)}
        onOptionSelect={(_e, data) => {
          handleConverterSelect(data.selectedOptions)
          setConverterSearch('')
        }}
        data-testid="converter-filter"
      >
        <OptionGroup label="Special">
          <Option value={NO_CONVERTERS_SENTINEL} text="(No converters)">(No converters)</Option>
        </OptionGroup>
        <OptionGroup label="Converters">
          {filteredConverterOptions.map((c) => (
            <Option key={c} value={c}>{c}</Option>
          ))}
        </OptionGroup>
      </Combobox>
      {showMatchModeToggle && (
        <Tooltip
          content={
            converterMatchMode === 'all'
              ? 'Attack must use ALL selected converters'
              : 'Attack must use ANY of the selected converters'
          }
          relationship="label"
        >
          <span className={styles.matchModeToggle}>
            <span className={styles.matchModeLabel}>Converters:</span>
            <span
              className={mergeClasses(
                styles.matchModeLabel,
                converterMatchMode === 'any' && styles.matchModeLabelActive,
              )}
              data-testid="converter-match-mode-label-any"
            >
              ANY
            </span>
            <Switch
              checked={converterMatchMode === 'all'}
              onChange={(_e, data) =>
                setFilter('converterMatchMode', data.checked ? 'all' : 'any')
              }
              aria-label={`Match ${converterMatchMode === 'all' ? 'all' : 'any'} selected converters`}
              data-testid="converter-match-mode-toggle"
            />
            <span
              className={mergeClasses(
                styles.matchModeLabel,
                converterMatchMode === 'all' && styles.matchModeLabelActive,
              )}
              data-testid="converter-match-mode-label-all"
            >
              ALL
            </span>
          </span>
        </Tooltip>
      )}
      <SearchableMultiCombobox
        className={styles.filterDropdown}
        placeholder="All operators"
        selectedOptions={operatorFilters}
        options={operatorOptions}
        onSelect={(selected) => setFilter('operator', selected)}
        testid="operator-filter"
      />
      <SearchableMultiCombobox
        className={styles.filterDropdown}
        placeholder="All operations"
        selectedOptions={operationFilters}
        options={operationOptions}
        onSelect={(selected) => setFilter('operation', selected)}
        testid="operation-filter"
      />
      <Combobox
        className={styles.filterDropdown}
        placeholder="Filter labels..."
        multiselect
        selectedOptions={otherLabelFilters}
        onOptionSelect={(_e, data) => {
          onFiltersChange({ ...filters, otherLabels: data.selectedOptions, labelSearchText: '' })
        }}
        value={labelSearchText}
        onChange={(e) => setFilter('labelSearchText', (e.target as HTMLInputElement).value)}
        data-testid="label-filter"
        freeform
      >
        {otherLabelOptions
          .filter(l => !labelSearchText || l.toLowerCase().includes(labelSearchText.toLowerCase()))
          .slice(0, 50)
          .map(l => (
            <Option key={l} value={l}>{l}</Option>
          ))}
        {otherLabelOptions.filter(l => !labelSearchText || l.toLowerCase().includes(labelSearchText.toLowerCase())).length > 50 && (
          <Option disabled value="__more" text={`Type to search more...`}>{`Type to search ${otherLabelOptions.length - 50} more...`}</Option>
        )}
      </Combobox>
    </div>
  )
}
