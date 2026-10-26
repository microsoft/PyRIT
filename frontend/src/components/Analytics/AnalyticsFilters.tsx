import { useId, useState } from 'react'

import {
  Button, Checkbox, Field, Input, MessageBar, MessageBarBody,
  Popover, PopoverSurface, PopoverTrigger, Select, Spinner, Text, mergeClasses,
} from '@fluentui/react-components'
import type { InputOnChangeData, SelectOnChangeData } from '@fluentui/react-components'
import { AddRegular, DismissRegular, FilterDismissRegular } from '@fluentui/react-icons'

import { useAnalyticsOperationLabels } from '@/hooks/useAnalyticsOperationLabels'
import { useAttackAnalyticsFacet } from '@/hooks/useAttackAnalyticsFacet'
import { useRuntime } from '@/hooks/useRuntime'
import type {
  AttackAnalyticsDimension, AttackAnalyticsFilter, AttackAnalyticsFilters,
  AttackAnalyticsOption, AttackAnalyticsValue, AttackOutcome,
} from '@/types'
import {
  ANALYTICS_MAX_VALUES, ANALYTICS_OUTCOMES, analyticsDateRangeError, analyticsDimensionKey, analyticsDimensionLabel,
  analyticsLabelEpoch, analyticsLegacyOperationLabel, analyticsOptionLabel, analyticsOptionLabelKey, analyticsValueKey,
  analyticsValueLabel, formatAnalyticsTime, hasAnalyticsFilters, withoutAnalyticsOperationIdLabels,
} from '@/utils/attackAnalytics'

import AnalyticsDimensionPicker from './AnalyticsDimensionPicker'
import { useAnalyticsFiltersStyles } from './AnalyticsFilters.styles'

interface AnalyticsFacetPickerProps {
  readonly dimension: AttackAnalyticsDimension
  readonly filters: AttackAnalyticsFilters
  readonly initial: AttackAnalyticsFilter | null
  readonly refreshVersion: number
  readonly knownLabels: Record<string, string>
  /** `fetchedOptions` are selections chosen from server facet options in this editor. */
  readonly onApply: (filter: AttackAnalyticsFilter, fetchedOptions: AttackAnalyticsOption[]) => void
}

/**
 * Server-form label for a restored value without a fetched label: analyticsOptionLabel adds the
 * absence suffixes, while a legacy name carries its own suffix as server labels do.
 */
function restoredOptionLabel(value: AttackAnalyticsValue): string {
  if (value.kind === 'missing') return 'Unknown'
  if (value.kind === 'no_converters') return 'No converters'
  if (value.kind === 'legacy_operation_name') return analyticsLegacyOperationLabel(value.value)
  return analyticsValueLabel(value)
}

/** Keep draft selections across facet searches/pages; only Apply changes the dashboard cohort. */
function AnalyticsFacetPicker({
  dimension, filters, initial, refreshVersion, knownLabels, onApply,
}: AnalyticsFacetPickerProps) {
  const styles = useAnalyticsFiltersStyles()
  const facet = useAttackAnalyticsFacet(dimension, filters, refreshVersion)
  // Restored selections may carry a fallback label, such as a raw Operation ID, so
  // Apply reports only options picked from the server's facet values for caching.
  const [restored] = useState<AttackAnalyticsOption[]>(() =>
    initial?.values.map((value: AttackAnalyticsValue) => ({
      key: value,
      label: knownLabels[analyticsOptionLabelKey(dimension, value)] ?? restoredOptionLabel(value),
    })) ?? [],
  )
  const [selected, setSelected] = useState(restored)
  const [matchMode, setMatchMode] = useState(initial?.match_mode ?? 'any')
  const tooMany = selected.length > ANALYTICS_MAX_VALUES
  const selectedKeys = new Set(selected.map((option: AttackAnalyticsOption) => analyticsValueKey(option.key)))

  function toggle(option: AttackAnalyticsOption): void {
    const key = analyticsValueKey(option.key)
    setSelected((previous: AttackAnalyticsOption[]) => {
      const remaining = previous.filter((item: AttackAnalyticsOption) => analyticsValueKey(item.key) !== key)
      return remaining.length === previous.length ? [...previous, option] : remaining
    })
  }

  return (
    <div className={styles.facet}>
      {dimension.name === 'converter_type' && (
        <Field label="Converter matching" className={styles.field}>
          <Select
            className={styles.input}
            value={matchMode}
            onChange={(_event: React.ChangeEvent<HTMLSelectElement>, data: SelectOnChangeData) => {
              if (data.value === 'any' || data.value === 'all') setMatchMode(data.value)
            }}
          >
            <option value="any">ANY selected converter</option>
            <option value="all">ALL selected converters</option>
          </Select>
        </Field>
      )}
      <Field label="Search values" className={styles.field}>
        <Input
          className={styles.input}
          name="analytics-facet-search"
          autoComplete="off"
          spellCheck={false}
          value={facet.search}
          maxLength={128}
          onChange={(_event: React.ChangeEvent<HTMLInputElement>, data: InputOnChangeData) => { facet.setSearch(data.value) }}
        />
      </Field>
      {selected.length > 0 && (
        <div role="group" aria-label="Selected filter values" className={styles.options}>
          {selected.map((option: AttackAnalyticsOption) => (
            <Checkbox
              className={styles.checkbox}
              key={analyticsValueKey(option.key)}
              label={analyticsOptionLabel(option)}
              checked
              onChange={() => { toggle(option) }}
            />
          ))}
        </div>
      )}
      {tooMany && <MessageBar intent="error"><MessageBarBody>Select at most 100 values in one filter.</MessageBarBody></MessageBar>}
      {!facet.ready ? <Text role="status">Filter values will load when the runtime is ready. Selections are preserved.</Text>
        : facet.loading ? <Spinner size="tiny" label="Loading filter values" /> : facet.error ? (
        <MessageBar intent="error">
          <MessageBarBody>
            Could not load filter values. {facet.error}{' '}
            <Button className={styles.button} onClick={facet.retry}>Retry values</Button>
          </MessageBarBody>
        </MessageBar>
      ) : (
        <>
          <div role="group" aria-label="Available filter values" className={styles.options}>
            {facet.items
              .filter((option: AttackAnalyticsOption) => !selectedKeys.has(analyticsValueKey(option.key)))
              .map((option: AttackAnalyticsOption) => (
                <Checkbox
                  className={styles.checkbox}
                  key={analyticsValueKey(option.key)}
                  label={analyticsOptionLabel(option)}
                  checked={false}
                  onChange={() => { toggle(option) }}
                />
              ))}
          </div>
          {facet.items.length === 0 && <Text>No values match this search. Selected values are retained.</Text>}
          <div className={styles.row}>
            <Button className={styles.button} disabled={facet.offset === 0} onClick={facet.firstPage}>First values</Button>
            <Button className={styles.button} disabled={!facet.hasMore} onClick={facet.nextPage}>Next values</Button>
          </div>
        </>
      )}
      <Text size={200} className={styles.note}>
        {matchMode === 'all' ? 'All selected converters must match.' : 'Selected values are alternatives within this filter.'}
        {' '}Separate filter chips are combined with AND.
        {dimension.name === 'operation' && ' Activity saved with only an Operation name is listed separately as (legacy name).'}
      </Text>
      <div>
        <Button
          appearance="primary"
          className={styles.button}
          disabled={selected.length === 0 || tooMany}
          onClick={() => {
            onApply(
              { dimension, values: selected.map((option: AttackAnalyticsOption) => option.key), match_mode: matchMode },
              selected.filter((option: AttackAnalyticsOption) => !restored.includes(option)),
            )
          }}
        >
          Apply filter
        </Button>
      </div>
    </div>
  )
}

interface AnalyticsFilterEditorProps {
  readonly initial: AttackAnalyticsFilter | null
  readonly startingDimension: AttackAnalyticsDimension | null
  readonly filters: AttackAnalyticsFilters
  readonly refreshVersion: number
  readonly knownLabels: Record<string, string>
  readonly onApply: (filter: AttackAnalyticsFilter, fetchedOptions: AttackAnalyticsOption[]) => void
  readonly onClose: () => void
}

/** Changing dimension remounts the picker so old selections cannot be applied to a different metadata field. */
function AnalyticsFilterEditor({
  initial, startingDimension, filters, refreshVersion, knownLabels, onApply, onClose,
}: AnalyticsFilterEditorProps) {
  const styles = useAnalyticsFiltersStyles()
  const [dimension, setDimension] = useState(startingDimension)
  const matchingInitial = initial && dimension &&
    analyticsDimensionKey(initial.dimension) === analyticsDimensionKey(dimension) ? initial : null
  return (
    <section aria-label="Filter editor" className={styles.editor}>
      <div className={styles.editorHeading}>
        <Text weight="semibold">{initial ? 'Edit filter' : 'Add filter'}</Text>
        <Button className={styles.iconButton} icon={<DismissRegular />} aria-label="Close filter editor" onClick={onClose} />
      </div>
      <AnalyticsDimensionPicker label="Filter dimension" dimension={dimension} onChange={setDimension} />
      {dimension ? (
        <AnalyticsFacetPicker
          key={analyticsDimensionKey(dimension)}
          dimension={dimension}
          filters={filters}
          initial={matchingInitial}
          knownLabels={knownLabels}
          refreshVersion={refreshVersion}
          onApply={onApply}
        />
      ) : <Text>Enter a custom label key and choose Use label to look up its values.</Text>}
    </section>
  )
}

interface AnalyticsDateFilterProps {
  readonly filters: AttackAnalyticsFilters
  readonly onChange: (filters: AttackAnalyticsFilters) => boolean
  readonly onClose: () => void
}

function AnalyticsDateFilter({ filters, onChange, onClose }: AnalyticsDateFilterProps) {
  const styles = useAnalyticsFiltersStyles()
  const [after, setAfter] = useState(filters.updated_after ?? '')
  const [before, setBefore] = useState(filters.updated_before ?? '')
  const [error, setError] = useState<string | null>(null)

  function apply(event: React.FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    const rangeError = analyticsDateRangeError(after || null, before || null)
    if (rangeError) {
      setError(rangeError)
      return
    }
    if (onChange({
      ...filters,
      updated_after: after || null,
      updated_before: before || null,
    })) onClose()
  }

  return (
    <form className={styles.editor} aria-label="Last updated range" onSubmit={apply}>
      <div className={styles.row}>
        <Field label="Last updated after" className={styles.field}>
          <Input className={styles.input} value={after} placeholder="2026-01-01T00:00:00Z" spellCheck={false}
            name="analytics-updated-after" autoComplete="off"
            onChange={(_event: React.ChangeEvent<HTMLInputElement>, data: InputOnChangeData) => { setAfter(data.value) }} />
        </Field>
        <Field label="Last updated before" className={styles.field}>
          <Input className={styles.input} value={before} placeholder="2026-02-01T00:00:00Z" spellCheck={false}
            name="analytics-updated-before" autoComplete="off"
            onChange={(_event: React.ChangeEvent<HTMLInputElement>, data: InputOnChangeData) => { setBefore(data.value) }} />
        </Field>
      </div>
      <Text size={200}>
        Use ISO 8601 with Z (UTC) or an explicit offset, such as 2026-11-01T01:30:00-07:00.
        After is inclusive; before is exclusive. This filters last updates, not attack execution time.
        Explicit offsets distinguish repeated local times during daylight saving changes.
      </Text>
      {error && <MessageBar intent="error"><MessageBarBody>{error}</MessageBarBody></MessageBar>}
      <div className={styles.row}>
        <Button className={styles.button} appearance="primary" type="submit">Apply range</Button>
        <Button className={styles.button} onClick={onClose}>Cancel</Button>
      </div>
    </form>
  )
}

interface AnalyticsFiltersProps {
  readonly filters: AttackAnalyticsFilters
  readonly refreshVersion: number
  readonly onChange: (filters: AttackAnalyticsFilters) => boolean
  /** Server labels of drilled-down options, keyed by `analyticsOptionLabelKey`. */
  readonly drilldownLabels?: Record<string, string>
}

interface FilterEditorState {
  readonly dimension: AttackAnalyticsDimension | null
  readonly index: number | null
  readonly initial: AttackAnalyticsFilter | null
}

/**
 * Edits whole-cohort predicates while keeping drafts and fetched labels local.
 * Existing chips edit one predicate; Add filter can append another on the same
 * dimension. The boolean onChange result keeps rejected edits open for correction.
 */
export default function AnalyticsFilters({ filters, refreshVersion, onChange, drilldownLabels }: AnalyticsFiltersProps) {
  const styles = useAnalyticsFiltersStyles()
  const runtime = useRuntime()
  const labelEpoch = analyticsLabelEpoch(refreshVersion, runtime.generation)
  const [editor, setEditor] = useState<FilterEditorState | null>(null)
  const [datesOpen, setDatesOpen] = useState(false)
  const [knownLabels, setKnownLabels] = useState<Record<string, string>>({})
  const [knownLabelsEpoch, setKnownLabelsEpoch] = useState(labelEpoch)
  // A rename changes only Operation names under a fixed key; other labels are immutable and nothing would refetch them.
  if (knownLabelsEpoch !== labelEpoch) {
    setKnownLabelsEpoch(labelEpoch)
    setKnownLabels((previous: Record<string, string>) => withoutAnalyticsOperationIdLabels(previous))
  }
  const operationLabels = useAnalyticsOperationLabels(filters, refreshVersion)
  // Within a label epoch, a facet pick is newer than the Operation lookup, so facet labels win, then drill-down
  // labels. A new epoch drops both caches' Operation names, so chips show the looked-up current names.
  const labels: Record<string, string> = {
    ...Object.fromEntries(Object.entries(operationLabels).map(([id, name]: [string, string]) => [
      analyticsOptionLabelKey({ name: 'operation' }, { kind: 'value', value: id }), name,
    ])),
    ...drilldownLabels,
    ...knownLabels,
  }
  const [expanded, setExpanded] = useState(false)
  const moreFiltersId = useId()
  // Browser Back can replace/remove a chip while its editor is open. Never apply
  // that stale draft to whichever predicate now occupies its former array index.
  const editorIsCurrent = editor?.index == null ||
    JSON.stringify(filters.dimensions[editor.index]) === JSON.stringify(editor.initial)
  const outcomesLabel = filters.outcomes.length === 0 || filters.outcomes.length === 4
    ? 'All' : filters.outcomes.join(', ')
  // Identical AND predicates are valid. Add an occurrence suffix for unique React
  // keys rather than deduplicating filters or identifying all chips by dimension.
  const occurrences = new Map<string, number>()

  function openDimension(dimension: AttackAnalyticsDimension): void {
    const index = filters.dimensions.findIndex(
      (predicate: AttackAnalyticsFilter) => analyticsDimensionKey(predicate.dimension) === analyticsDimensionKey(dimension),
    )
    setDatesOpen(false)
    setEditor({ dimension, index: index < 0 ? null : index, initial: filters.dimensions[index] ?? null })
  }

  function apply(filter: AttackAnalyticsFilter, fetchedOptions: AttackAnalyticsOption[]): void {
    const dimensions = editor?.index == null
      ? [...filters.dimensions, filter]
      : filters.dimensions.map((predicate: AttackAnalyticsFilter, index: number) => index === editor.index ? filter : predicate)
    if (!onChange({ ...filters, dimensions })) return
    setKnownLabels((previous: Record<string, string>) => ({
      ...previous,
      ...Object.fromEntries(fetchedOptions.map((option: AttackAnalyticsOption) => [
        analyticsOptionLabelKey(filter.dimension, option.key), option.label,
      ])),
    }))
    setEditor(null)
  }

  return (
    <section aria-label="Analytics filters" className={styles.root}>
      <div className={styles.row}>
        <Button className={styles.button} onClick={() => { openDimension({ name: 'operation' }) }}>Operation</Button>
        <Button className={styles.button} onClick={() => { openDimension({ name: 'operator' }) }}>Operator</Button>
        <Button className={styles.button} onClick={() => { openDimension({ name: 'objective_target' }) }}>Objective target</Button>
        <Button className={styles.disclosure} aria-expanded={expanded} aria-controls={moreFiltersId}
          onClick={() => { setExpanded(!expanded) }}>{expanded ? 'Fewer filters' : 'More filters'}</Button>
        <div id={moreFiltersId} className={mergeClasses(styles.secondaryControls, !expanded && styles.collapsed)}>
        <Popover>
          <PopoverTrigger disableButtonEnhancement>
            <Button className={styles.button}>Outcomes: {outcomesLabel}</Button>
          </PopoverTrigger>
          <PopoverSurface className={styles.popover} aria-label="Outcome filter">
            <Text>Select outcomes for the entire dashboard. No selection includes all outcomes.</Text>
            {ANALYTICS_OUTCOMES.map((outcome: AttackOutcome) => (
              <Checkbox className={styles.checkbox} key={outcome} label={outcome} checked={filters.outcomes.includes(outcome)}
                onChange={() => {
                  onChange({ ...filters, outcomes: filters.outcomes.includes(outcome)
                    ? filters.outcomes.filter((item: AttackOutcome) => item !== outcome) : [...filters.outcomes, outcome] })
                }} />
            ))}
            <Button className={styles.button} onClick={() => { onChange({ ...filters, outcomes: [] }) }}>All outcomes</Button>
          </PopoverSurface>
        </Popover>
        <Button className={styles.button} icon={<AddRegular />}
          onClick={() => { setDatesOpen(false); setEditor({ dimension: { name: 'attack_type' }, index: null, initial: null }) }}>
          Add filter
        </Button>
        <Button className={styles.button} aria-expanded={datesOpen}
          onClick={() => { setEditor(null); setDatesOpen(!datesOpen) }}>Last updated</Button>
        <Button className={styles.button} appearance="subtle" icon={<FilterDismissRegular />}
          disabled={!hasAnalyticsFilters(filters)}
          onClick={() => {
            if (onChange({ dimensions: [], outcomes: [] })) { setEditor(null); setDatesOpen(false) }
          }}>Clear all filters</Button>
        </div>
      </div>
      <div className={styles.row} aria-label="Active filters">
        {filters.dimensions.map((predicate: AttackAnalyticsFilter, index: number) => {
          const fingerprint = JSON.stringify(predicate)
          const occurrence = occurrences.get(fingerprint) ?? 0
          occurrences.set(fingerprint, occurrence + 1)
          const label = `${analyticsDimensionLabel(predicate.dimension)} (${predicate.match_mode.toUpperCase()}): ` +
            predicate.values.map((value: AttackAnalyticsValue) =>
              labels[analyticsOptionLabelKey(predicate.dimension, value)]
                ? analyticsOptionLabel({ key: value, label: labels[analyticsOptionLabelKey(predicate.dimension, value)] })
                : analyticsValueLabel(value),
            ).join(', ')
          return (
            <div key={`${fingerprint}:${occurrence}`} className={styles.chip}>
              <Button appearance="subtle" className={styles.chipLabel}
                onClick={() => {
                  setDatesOpen(false)
                  setEditor({ dimension: predicate.dimension, index, initial: predicate })
                }}><span className={styles.chipText} title={label}>{label}</span></Button>
              <Button appearance="subtle" className={styles.iconButton} icon={<DismissRegular />} aria-label={`Remove ${label}`}
                onClick={() => {
                  setEditor(null)
                  onChange({ ...filters, dimensions: filters.dimensions.filter((_filter: AttackAnalyticsFilter, at: number) => at !== index) })
                }} />
            </div>
          )
        })}
        {filters.outcomes.map((outcome: AttackOutcome) => (
          <Button key={outcome} className={styles.button} icon={<DismissRegular />} aria-label={`Remove outcome ${outcome}`}
            onClick={() => { onChange({ ...filters, outcomes: filters.outcomes.filter((item: AttackOutcome) => item !== outcome) }) }}>
            Outcome: {outcome}
          </Button>
        ))}
        {filters.updated_after && (
          <Button className={styles.button} icon={<DismissRegular />} aria-label="Remove last updated after"
            onClick={() => { onChange({ ...filters, updated_after: null }) }}>Updated after: {formatAnalyticsTime(filters.updated_after)}</Button>
        )}
        {filters.updated_before && (
          <Button className={styles.button} icon={<DismissRegular />} aria-label="Remove last updated before"
            onClick={() => { onChange({ ...filters, updated_before: null }) }}>Updated before: {formatAnalyticsTime(filters.updated_before)}</Button>
        )}
      </div>
      {editor && !editorIsCurrent && <Text role="status">The edited filter changed. Reopen a filter chip to edit the current selection.</Text>}
      {editor && editorIsCurrent && (
        <AnalyticsFilterEditor
          key={JSON.stringify(editor)}
          initial={editor.initial} startingDimension={editor.dimension} filters={filters}
          knownLabels={labels} refreshVersion={refreshVersion}
          onApply={apply} onClose={() => { setEditor(null) }}
        />
      )}
      {datesOpen && <AnalyticsDateFilter key={JSON.stringify([filters.updated_after, filters.updated_before])}
        filters={filters} onChange={onChange} onClose={() => { setDatesOpen(false) }} />}
    </section>
  )
}
