import {
  Button,
  Tooltip,
  Option,
  OptionGroup,
  Combobox,
  Switch,
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
// we have to drive the displayed text via `value` ourselves.
function formatMultiSelectValue(selected: string[]): string {
  if (selected.length === 0) return ''
  if (selected.length === 1) return selected[0]
  return `${selected[0]} (+${selected.length - 1})`
}

interface HistoryFiltersBarProps {
  filters: HistoryFilters
  onFiltersChange: (filters: HistoryFilters) => void
  attackClassOptions: string[]
  converterOptions: string[]
  operatorOptions: string[]
  operationOptions: string[]
  otherLabelOptions: string[]
}

export default function HistoryFiltersBar({
  filters,
  onFiltersChange,
  attackClassOptions,
  converterOptions,
  operatorOptions,
  operationOptions,
  otherLabelOptions,
}: HistoryFiltersBarProps) {
  const styles = useAttackHistoryStyles()

  const {
    attackClasses: attackClassFilters,
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
    attackClassFilters.length > 0 ||
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
      <Combobox
        className={styles.filterDropdown}
        placeholder="All attack types"
        multiselect
        selectedOptions={attackClassFilters}
        value={formatMultiSelectValue(attackClassFilters)}
        onOptionSelect={(_e, data) => setFilter('attackClasses', data.selectedOptions)}
        data-testid="attack-class-filter"
      >
        {attackClassOptions.map((cls) => (
          <Option key={cls} value={cls}>{cls}</Option>
        ))}
      </Combobox>
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
        selectedOptions={converterSelectedOptions}
        value={
          hasConverters === false
            ? '(No converters)'
            : formatMultiSelectValue(converterFilter)
        }
        onOptionSelect={(_e, data) => handleConverterSelect(data.selectedOptions)}
        data-testid="converter-filter"
      >
        <OptionGroup label="Special">
          <Option value={NO_CONVERTERS_SENTINEL} text="(No converters)">(No converters)</Option>
        </OptionGroup>
        <OptionGroup label="Converters">
          {converterOptions.map((c) => (
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
          <Switch
            label={converterMatchMode === 'all' ? 'Match all' : 'Match any'}
            checked={converterMatchMode === 'all'}
            onChange={(_e, data) =>
              setFilter('converterMatchMode', data.checked ? 'all' : 'any')
            }
            data-testid="converter-match-mode-toggle"
          />
        </Tooltip>
      )}
      <Combobox
        className={styles.filterDropdown}
        placeholder="All operators"
        multiselect
        selectedOptions={operatorFilters}
        value={formatMultiSelectValue(operatorFilters)}
        onOptionSelect={(_e, data) => setFilter('operator', data.selectedOptions)}
        data-testid="operator-filter"
      >
        {operatorOptions.map((o) => (
          <Option key={o} value={o}>{o}</Option>
        ))}
      </Combobox>
      <Combobox
        className={styles.filterDropdown}
        placeholder="All operations"
        multiselect
        selectedOptions={operationFilters}
        value={formatMultiSelectValue(operationFilters)}
        onOptionSelect={(_e, data) => setFilter('operation', data.selectedOptions)}
        data-testid="operation-filter"
      >
        {operationOptions.map((o) => (
          <Option key={o} value={o}>{o}</Option>
        ))}
      </Combobox>
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
