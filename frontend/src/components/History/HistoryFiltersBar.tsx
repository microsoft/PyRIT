import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Button,
  Tooltip,
  Option,
  OptionGroup,
  Combobox,
  Switch,
  mergeClasses,
} from '@fluentui/react-components'
import { useActiveDescendantContext } from '@fluentui/react-aria'
import type { ActiveDescendantImperativeRef } from '@fluentui/react-aria'
import { FilterDismissRegular } from '@fluentui/react-icons'
import { Virtualizer, useStaticVirtualizerMeasure } from '@fluentui-contrib/react-virtualizer'
import SearchableMultiCombobox from '@/components/SearchableMultiCombobox'
import type { FilterOption } from '@/types'
import { DEFAULT_HISTORY_FILTERS } from './historyFilters'
import type { HistoryFilters } from './historyFilters'
import { useAttackHistoryStyles } from './AttackHistory.styles'

const LABEL_OPTION_HEIGHT_PX = 32
// Rendered before the listbox has been measured (jsdom never fires the observer).
const LABEL_FALLBACK_RENDERED_OPTIONS = 12
// Marks each option with its index in the filtered list so Home/End can locate
// it in the DOM once the virtualizer mounts it.
const LABEL_OPTION_INDEX_ATTR = 'data-label-index'
// The virtualizer mounts newly revealed options after a React commit, so Home/End
// activation retries on animation frames until the target option exists.
const LABEL_ACTIVATION_ATTEMPTS = 30

const scheduleLabelActivation = (step: () => void) => {
  if (typeof requestAnimationFrame === 'function') {
    requestAnimationFrame(step)
  } else {
    setTimeout(step, 16)
  }
}

/**
 * Fluent drives combobox keyboard navigation through an active descendant
 * controller installed around the listbox options. The virtualized label list
 * needs that controller for Home and End, so this component reads it from
 * context and hands it to the filter bar through a ref. It renders nothing.
 */
function LabelActiveDescendantBridge({
  controllerRef,
}: {
  controllerRef: React.RefObject<ActiveDescendantImperativeRef | null>
}) {
  const { controller } = useActiveDescendantContext()
  useEffect(() => {
    controllerRef.current = controller
    return () => {
      controllerRef.current = null
    }
  }, [controller, controllerRef])
  return null
}

const NO_CONVERTERS_SENTINEL = '__no_converters__'

const OUTCOME_LABELS: Record<string, string> = {
  success: 'Success',
  failure: 'Failure',
  error: 'Error',
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

/** History's choices are plain strings, shown as they are. */
function toFilterOptions(values: string[]): FilterOption[] {
  return values.map((value: string) => ({ value, label: value }))
}

interface HistoryFiltersBarProps {
  filters: HistoryFilters
  onFiltersChange: (filters: HistoryFilters) => void
  attackTypeOptions: string[]
  converterOptions: string[]
  operatorOptions: string[]
  operationOptions: FilterOption[]
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
    includeScenarioAttacks,
    operator: operatorFilters,
    operationId: operationFilters,
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
    !includeScenarioAttacks ||
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
  const filteredLabelOptions = otherLabelOptions.filter(
    l => !labelSearchText || l.toLowerCase().includes(labelSearchText.toLowerCase()),
  )
  // The label list can reach hundreds of entries, so it renders through a virtualizer
  // instead of capping the visible options and asking the user to search further.
  // The virtualized window scrolls inside its own bounded surface: autoSize gives
  // the popup an inline max height of its own, which would override a max height
  // set on the listbox element, so the scroll container is a child Fluent never
  // styles.
  const labelListMeasure = useStaticVirtualizerMeasure<HTMLElement>({
    defaultItemSize: LABEL_OPTION_HEIGHT_PX,
    direction: 'vertical',
  })
  const labelListScrollRef = labelListMeasure.scrollRef
  const [labelOpen, setLabelOpen] = useState(false)
  const labelActiveDescendant = useRef<ActiveDescendantImperativeRef | null>(null)
  const labelScrollSurface = useRef<HTMLDivElement | null>(null)
  // Identifies the newest Home/End activation request so stale retries abort.
  const labelActivationRequest = useRef(0)
  const setLabelScrollSurface = useCallback(
    (el: HTMLDivElement | null) => {
      labelScrollSurface.current = el
      labelListScrollRef(el)
    },
    [labelListScrollRef],
  )

  // Re-anchor the list at the top when the popup opens or the search changes:
  // the scroll surface survives close and reopen, so without this a reopened
  // popup would resume partway down the previous result set.
  useEffect(() => {
    // Invalidate any pending activation along with the re-anchor; a retry
    // that fires after unmount finds the surface ref already cleared.
    labelActivationRequest.current++
    if (labelOpen) labelScrollSurface.current?.scrollTo({ top: 0 })
  }, [labelOpen, labelSearchText])

  // Fluent's Home and End walk only the options currently mounted in the DOM,
  // which inside a virtualized list is the visible window instead of the whole
  // filtered set. Handle both keys against the full list: jump the scroll
  // surface so the target option mounts, then activate it through the same
  // controller Fluent's arrow navigation uses.
  const activateLabelOption = (requestId: number, targetIndex: number, attemptsLeft: number) => {
    if (requestId !== labelActivationRequest.current) return
    const surface = labelScrollSurface.current
    const target = surface?.querySelector<HTMLElement>(`[${LABEL_OPTION_INDEX_ATTR}="${targetIndex}"]`)
    if (target) {
      labelActiveDescendant.current?.focus(target.id)
    } else if (attemptsLeft > 0) {
      scheduleLabelActivation(() => activateLabelOption(requestId, targetIndex, attemptsLeft - 1))
    }
  }

  const handleLabelKeyDown = (event: React.KeyboardEvent<HTMLElement>) => {
    if (!labelOpen || (event.key !== 'Home' && event.key !== 'End')) return
    event.preventDefault()
    event.stopPropagation()
    const targetIndex = event.key === 'Home' ? 0 : filteredLabelOptions.length - 1
    if (targetIndex < 0) return
    const surface = labelScrollSurface.current
    if (surface) surface.scrollTop = targetIndex * LABEL_OPTION_HEIGHT_PX
    activateLabelOption(++labelActivationRequest.current, targetIndex, LABEL_ACTIVATION_ATTEMPTS)
  }

  return (
    <div className={styles.filters}>
      <div className={styles.filterRow}>
        <Tooltip content="Reset all filters" relationship="label">
          <Button
            className={styles.touchTargetHeight}
            appearance="subtle"
            size="small"
            icon={<FilterDismissRegular />}
            aria-label="Reset all filters"
            disabledFocusable={!hasActiveFilters}
            onClick={() => onFiltersChange({ ...DEFAULT_HISTORY_FILTERS })}
            data-testid="reset-filters-btn"
          />
        </Tooltip>
        <SearchableMultiCombobox
          className={styles.filterDropdown}
          placeholder="All operators"
          selectedOptions={operatorFilters}
          options={toFilterOptions(operatorOptions)}
          onSelect={(selected) => setFilter('operator', selected)}
          testId="operator-filter"
        />
        <SearchableMultiCombobox
          className={styles.filterDropdown}
          placeholder="All operations"
          selectedOptions={operationFilters}
          options={operationOptions}
          onSelect={(selected) => setFilter('operationId', selected)}
          testId="operation-filter"
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
          <Option value="error">Error</Option>
          <Option value="undetermined">Undetermined</Option>
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
          onOpenChange={(_e, data) => setLabelOpen(data.open)}
          onKeyDownCapture={handleLabelKeyDown}
          data-testid="label-filter"
          freeform
          positioning={{ autoSize: true }}
          listbox={{
            onKeyDownCapture: handleLabelKeyDown,
          }}
        >
          <LabelActiveDescendantBridge controllerRef={labelActiveDescendant} />
          <div
            ref={setLabelScrollSurface}
            className={styles.labelScrollSurface}
            data-testid="label-list-scroll-surface"
          >
            <Virtualizer
              numItems={filteredLabelOptions.length}
              virtualizerLength={labelListMeasure.virtualizerLength || LABEL_FALLBACK_RENDERED_OPTIONS}
              bufferItems={labelListMeasure.bufferItems}
              bufferSize={labelListMeasure.bufferSize}
              itemSize={LABEL_OPTION_HEIGHT_PX}
              containerSizeRef={labelListMeasure.containerSizeRef}
            >
              {(index: number) => (
                <Option
                  key={filteredLabelOptions[index]}
                  value={filteredLabelOptions[index]}
                  aria-posinset={index + 1}
                  aria-setsize={filteredLabelOptions.length}
                  {...{ [LABEL_OPTION_INDEX_ATTR]: index }}
                >
                  {filteredLabelOptions[index]}
                </Option>
              )}
            </Virtualizer>
          </div>
        </Combobox>
        <SearchableMultiCombobox
          className={styles.filterDropdown}
          placeholder="All attack types"
          selectedOptions={attackTypeFilters}
          options={toFilterOptions(attackTypeOptions)}
          onSelect={(selected) => setFilter('attackTypes', selected)}
          testId="attack-type-filter"
        />
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
      </div>
      <div className={styles.secondaryFilterRow} data-testid="scanner-attack-filter-row">
        <Switch
          checked={includeScenarioAttacks}
          onChange={(_event, data) => setFilter('includeScenarioAttacks', data.checked)}
          label="Include scanner attacks"
          data-testid="include-scanner-attacks"
        />
      </div>
    </div>
  )
}
