import {
  Button,
  Tooltip,
  Dropdown,
  Option,
  Combobox,
} from '@fluentui/react-components'
import {
  FilterRegular,
  FilterDismissRegular,
} from '@fluentui/react-icons'
import { DEFAULT_HISTORY_FILTERS } from './historyFilters'
import type { HistoryFilters } from './historyFilters'
import { useAttackHistoryStyles } from './AttackHistory.styles'

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
    attackClass: attackClassFilter,
    outcome: outcomeFilter,
    converter: converterFilter,
    operator: operatorFilter,
    operation: operationFilter,
    otherLabels: otherLabelFilters,
    labelSearchText,
  } = filters

  const setFilter = <K extends keyof HistoryFilters>(key: K, value: HistoryFilters[K]) => {
    onFiltersChange({ ...filters, [key]: value })
  }

  const hasActiveFilters =
    attackClassFilter || outcomeFilter || converterFilter ||
    operatorFilter || operationFilter || otherLabelFilters.length > 0

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
      <Dropdown
        className={styles.filterDropdown}
        placeholder="All attack types"
        value={attackClassFilter || undefined}
        selectedOptions={attackClassFilter ? [attackClassFilter] : []}
        onOptionSelect={(_e, data) => setFilter('attackClass', data.optionValue ?? '')}
        data-testid="attack-class-filter"
      >
        <Option value="">All attack types</Option>
        {attackClassOptions.map(cls => (
          <Option key={cls} value={cls}>{cls}</Option>
        ))}
      </Dropdown>
      <Dropdown
        className={styles.filterDropdown}
        placeholder="All outcomes"
        value={outcomeFilter || undefined}
        selectedOptions={outcomeFilter ? [outcomeFilter] : []}
        onOptionSelect={(_e, data) => setFilter('outcome', data.optionValue ?? '')}
        data-testid="outcome-filter"
      >
        <Option value="">All outcomes</Option>
        <Option value="success">Success</Option>
        <Option value="failure">Failure</Option>
        <Option value="undetermined">Undetermined</Option>
      </Dropdown>
      <Dropdown
        className={styles.filterDropdown}
        placeholder="All converters"
        value={converterFilter || undefined}
        selectedOptions={converterFilter ? [converterFilter] : []}
        onOptionSelect={(_e, data) => setFilter('converter', data.optionValue ?? '')}
        data-testid="converter-filter"
      >
        <Option value="">All converters</Option>
        {converterOptions.map(c => (
          <Option key={c} value={c}>{c}</Option>
        ))}
      </Dropdown>
      <Dropdown
        className={styles.filterDropdown}
        placeholder="All operators"
        value={operatorFilter || undefined}
        selectedOptions={operatorFilter ? [operatorFilter] : []}
        onOptionSelect={(_e, data) => setFilter('operator', data.optionValue ?? '')}
        data-testid="operator-filter"
      >
        <Option value="">All operators</Option>
        {operatorOptions.map(o => (
          <Option key={o} value={o}>{o}</Option>
        ))}
      </Dropdown>
      <Dropdown
        className={styles.filterDropdown}
        placeholder="All operations"
        value={operationFilter || undefined}
        selectedOptions={operationFilter ? [operationFilter] : []}
        onOptionSelect={(_e, data) => setFilter('operation', data.optionValue ?? '')}
        data-testid="operation-filter"
      >
        <Option value="">All operations</Option>
        {operationOptions.map(o => (
          <Option key={o} value={o}>{o}</Option>
        ))}
      </Dropdown>
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
