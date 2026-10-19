import { useEffect, useId, useMemo, useRef } from 'react'
import type { KeyboardEvent } from 'react'

import {
  Badge,
  Button,
  Input,
  MessageBar,
  MessageBarBody,
  Select,
  Spinner,
  Table,
  TableBody,
  TableCell,
  TableHeader,
  TableHeaderCell,
  TableRow,
  Text,
} from '@fluentui/react-components'
import { ArrowSyncRegular, ChevronLeftRegular, ChevronRightRegular, SearchRegular } from '@fluentui/react-icons'

import SearchableMultiCombobox from '@/components/SearchableMultiCombobox'
import { useSeedExampleDetail, useSeedExamples } from '@/hooks/useSeedExamples'
import type { DatasetInfo, FilterOption, SeedExampleSummary } from '@/types'

import SeedExampleDetail from './SeedExampleDetail'
import { useSeedExampleBrowserStyles } from './SeedExampleBrowser.styles'
import {
  clearedSeedFilters,
  DEFAULT_SEED_PAGE_SIZE,
  popSeedCursor,
  pushSeedCursor,
  SEED_PAGE_SIZE_OPTIONS,
  SEED_TYPE_LABELS,
  SEED_TYPES,
  seedFiltersActive,
  seedPageNumber,
  seedQueryWithoutPaging,
  type SeedBrowserQuery,
} from './seedExampleQuery'
import { singleSeedExampleChoice } from './seedExampleSelection'
import { useSeedExampleBrowserState, type SeedExampleBrowserState } from './useSeedExampleBrowserState'

const NOT_LABELED_LABEL = 'Not labeled'
const SEARCH_HELP = 'Search looks through stored text prompts and objectives. Simulated-conversation JSON is not included; choose that seed type to find those examples. ASCII letters ignore case.'

interface SeedExampleBrowserProps {
  dataset: DatasetInfo
  onCatalogStale: () => void
}

function toOptions(values: readonly string[]): FilterOption[] {
  const seen = new Set<string>()
  const options: FilterOption[] = []
  for (const value of values) {
    if (!value || seen.has(value)) continue
    seen.add(value)
    options.push({ value, label: value })
  }
  options.sort((left, right) => left.label.localeCompare(right.label, 'en', { sensitivity: 'base' }))
  return options
}

function rowKeys(items: readonly SeedExampleSummary[]): string[] {
  const seen = new Map<string, number>()
  const keys: string[] = []
  for (const item of items) {
    const count = seen.get(item.example_id) ?? 0
    seen.set(item.example_id, count + 1)
    keys.push(count === 0 ? item.example_id : `${item.example_id}#${count}`)
  }
  return keys
}

function exampleStatus(loading: boolean, failed: boolean, total: number | null, filtered: boolean, page: number | null): string {
  let label = 'Loading examples...'
  if (!loading && failed) label = 'Could not load examples.'
  else if (!loading && total === 0) label = filtered ? 'No matching examples' : 'No examples in this dataset'
  else if (!loading && total === 1) label = '1 matching example'
  else if (!loading && total != null) label = `${total.toLocaleString('en-US')} matching examples`
  if (page == null || loading || failed || total == null || total === 0) return label
  return `${label}, page ${page}`
}

function pageSizeChoices(limit: number): number[] {
  const choices: number[] = [...SEED_PAGE_SIZE_OPTIONS]
  if (!choices.some((choice) => choice === limit)) choices.push(limit)
  choices.sort((left, right) => left - right)
  return choices
}

/** Read-only example table for one loaded dataset. */
export default function SeedExampleBrowser({ dataset, onCatalogStale }: SeedExampleBrowserProps) {
  const browserState = useSeedExampleBrowserState(dataset.selection_key)
  return <SeedExampleBrowserView dataset={dataset} onCatalogStale={onCatalogStale} browserState={browserState} />
}

export function SeedExampleBrowserView({
  dataset,
  onCatalogStale,
  browserState,
}: SeedExampleBrowserProps & { browserState: SeedExampleBrowserState }) {
  const styles = useSeedExampleBrowserStyles()
  const helpId = useId()
  const { query, visitedCursors, generation, commit } = browserState
  const pageState = useSeedExamples(dataset.selection_key, query, generation)
  const detailState = useSeedExampleDetail(
    dataset.selection_key,
    query.exampleId,
    query.exampleRepeated,
    generation,
  )
  const choice = query.exampleId
    ? singleSeedExampleChoice(dataset.selection_key, query.exampleId)
    : null
  const listRetryRef = useRef<HTMLButtonElement>(null)
  const cursorButtonRef = useRef<HTMLButtonElement>(null)
  const detailHeadingRef = useRef<HTMLHeadingElement>(null)
  const missingExampleRef = useRef<HTMLParagraphElement>(null)
  const detailRetryRef = useRef<HTMLButtonElement>(null)
  const moveFocusToDetail = useRef(false)
  const buttonRefs = useRef(new Map<string, HTMLButtonElement | HTMLAnchorElement>())
  const staleRefreshKey = useRef<string | null>(null)

  const exampleMissing = query.exampleId != null
    && !query.exampleRepeated
    && !detailState.loading
    && detailState.error == null
    && (
      detailState.notFound
      || (detailState.detail != null && detailState.detail.example_id !== query.exampleId)
    )

  useEffect(() => {
    if (pageState.error) listRetryRef.current?.focus()
  }, [pageState.error])

  useEffect(() => {
    if (query.cursorRepeated) cursorButtonRef.current?.focus()
  }, [query.cursorRepeated])

  useEffect(() => {
    if (!moveFocusToDetail.current || detailState.loading) return
    if (detailState.detail && detailState.detail.example_id === query.exampleId) detailHeadingRef.current?.focus()
    else if (exampleMissing) missingExampleRef.current?.focus()
    else if (detailState.error) detailRetryRef.current?.focus()
    else return
    moveFocusToDetail.current = false
  }, [detailState.detail, detailState.error, detailState.loading, exampleMissing, query.exampleId])

  useEffect(() => {
    const key = `${generation}:${dataset.selection_key}`
    const emptyDisagrees = pageState.page != null
      && pageState.page.total === 0
      && !seedFiltersActive(query)
      && query.cursor == null
      && !query.cursorRepeated
      && dataset.logical_examples != null
      && dataset.logical_examples > 0
    const selectionRejected = pageState.error?.status === 400 && query.cursor == null && !query.cursorRepeated
    if ((emptyDisagrees || selectionRejected) && staleRefreshKey.current !== key) {
      staleRefreshKey.current = key
      onCatalogStale()
    }
  }, [
    dataset.logical_examples,
    dataset.selection_key,
    generation,
    onCatalogStale,
    pageState.error,
    pageState.page,
    query,
  ])

  const items = pageState.page?.items ?? []
  const keys = rowKeys(items)
  const selectedOnPage = items.some((item) => item.example_id === choice?.exampleId)
  const filtered = seedFiltersActive(query)
  const cursorRejected = query.cursorRepeated || (pageState.error?.status === 400 && query.cursor != null)
  const pageNumber = seedPageNumber(query, visitedCursors)
  const status = query.cursorRepeated
    ? 'This page link has more than one cursor.'
    : exampleStatus(pageState.loading, pageState.error != null, pageState.page?.total ?? null, filtered, pageNumber)
  const modalityOptions = useMemo(() => toOptions(dataset.modalities), [dataset.modalities])
  const harmOptions = useMemo(() => toOptions(dataset.harm_categories), [dataset.harm_categories])
  const seedTypeOptions = SEED_TYPES.map((seedType) => ({ value: seedType, label: SEED_TYPE_LABELS[seedType] }))

  const updateFilters = (next: SeedBrowserQuery): void => {
    commit({ ...next, cursor: null, cursorRepeated: false }, [], true)
  }

  const selectExample = (exampleId: string | null, focusDetail: boolean): void => {
    moveFocusToDetail.current = focusDetail && exampleId != null
    commit({ ...query, exampleId, exampleRepeated: false }, visitedCursors, true)
  }

  const showNext = (): void => {
    const nextCursor = pageState.page?.pagination.next_cursor
    if (!pageState.page?.pagination.has_more || !nextCursor) return
    commit(
      { ...query, cursor: nextCursor, cursorRepeated: false },
      pushSeedCursor(visitedCursors, query.cursor),
      false,
    )
  }

  const showPrevious = (): void => {
    const previous = popSeedCursor(visitedCursors)
    if (!previous) return
    commit(
      { ...query, cursor: previous.cursor, cursorRepeated: false },
      previous.seedCursors,
      false,
    )
  }

  const restartFromFirstPage = (): void => {
    commit(seedQueryWithoutPaging(query), [], true)
  }

  const onExampleKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number): void => {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return
    event.preventDefault()
    const nextIndex = event.key === 'ArrowDown' ? index + 1 : index - 1
    const next = items[nextIndex]
    const nextKey = keys[nextIndex]
    if (!next || !nextKey) return
    selectExample(next.example_id, false)
    buttonRefs.current.get(nextKey)?.focus()
  }

  return (
    <div className={styles.root} data-selected-example={choice ? `${choice.selectionKey} ${choice.exampleId}` : ''}>
      <div className={styles.filters}>
        <Input
          className={styles.search}
          contentBefore={<SearchRegular />}
          placeholder="Search prompts and objectives"
          value={query.search}
          maxLength={1000}
          aria-label="Search examples"
          aria-describedby={helpId}
          onChange={(_event, data) => { updateFilters({ ...query, search: data.value }) }}
        />
        <SearchableMultiCombobox
          className={styles.filterControl}
          ariaLabel="Example modalities"
          placeholder="All modalities"
          summaryPrefix="Modalities"
          options={modalityOptions}
          selectedOptions={[...query.modalities]}
          onSelect={(selected) => { updateFilters({ ...query, modalities: selected }) }}
          testId="example-modality-filter"
        />
        <SearchableMultiCombobox
          className={styles.filterControl}
          ariaLabel="Example seed types"
          placeholder="All seed types"
          summaryPrefix="Seed types"
          options={seedTypeOptions}
          selectedOptions={[...query.seedTypes]}
          onSelect={(selected) => {
            updateFilters({
              ...query,
              seedTypes: selected.filter((value): value is SeedBrowserQuery['seedTypes'][number] => (
                SEED_TYPES.some((seedType) => seedType === value)
              )),
            })
          }}
          testId="example-seed-type-filter"
        />
        <SearchableMultiCombobox
          className={styles.filterControl}
          ariaLabel="Example harm categories"
          placeholder="All harm categories"
          summaryPrefix="Harm categories"
          options={harmOptions}
          selectedOptions={[...query.harmCategories]}
          onSelect={(selected) => { updateFilters({ ...query, harmCategories: selected }) }}
          testId="example-harm-filter"
        />
        <Select
          className={styles.filterControl}
          aria-label="Examples per page"
          value={String(query.limit)}
          onChange={(_event, data) => {
            const limit = Number(data.value)
            updateFilters({
              ...query,
              limit: Number.isInteger(limit) ? limit : DEFAULT_SEED_PAGE_SIZE,
            })
          }}
        >
          {pageSizeChoices(query.limit).map((limit) => (
            <option key={limit} value={limit}>{limit} per page</option>
          ))}
        </Select>
        {filtered && (
          <Button className={styles.touchTarget} appearance="subtle" onClick={() => { updateFilters(clearedSeedFilters(query)) }}>
            Clear filters
          </Button>
        )}
      </div>
      <p id={helpId} className={styles.help}>{SEARCH_HELP}</p>
      <p className={styles.help}>Newest examples are listed first. This view does not run examples, import them into chat, or play media.</p>
      <p className={styles.status} role="status" aria-live="polite" data-testid="example-status">{status}</p>

      {query.cursorRepeated ? (
        <div>
          <p className={styles.message}>Choose one page link, or start again from the first page.</p>
          <Button
            ref={cursorButtonRef}
            className={styles.touchTarget}
            appearance="primary"
            onClick={restartFromFirstPage}
          >
            Start from the first page
          </Button>
        </div>
      ) : pageState.loading ? (
        <Spinner label="Loading examples..." />
      ) : pageState.error ? (
        <div>
          <MessageBar intent="error">
            <MessageBarBody>
              {cursorRejected
                ? 'This page link is not valid for these filters.'
                : pageState.error.detail}
            </MessageBarBody>
          </MessageBar>
          <Button
            ref={listRetryRef}
            className={styles.touchTarget}
            appearance="primary"
            icon={<ArrowSyncRegular />}
            onClick={pageState.retry}
          >
            Retry
          </Button>
          {cursorRejected && (
            <Button
              className={styles.touchTarget}
              appearance="primary"
              onClick={restartFromFirstPage}
            >
              Start from the first page
            </Button>
          )}
        </div>
      ) : pageState.page && pageState.page.total === 0 ? (
        <p className={styles.message}>
          {filtered ? 'Clear the search or filters to see every example in this dataset.' : 'This dataset has no stored examples.'}
        </p>
      ) : (
        <div className={styles.tableWrap}>
          <Table className={styles.table} aria-label="Examples">
            <TableHeader>
              <TableRow>
                <TableHeaderCell className={styles.cell}>Example</TableHeaderCell>
                <TableHeaderCell className={styles.cell}>Modalities</TableHeaderCell>
                <TableHeaderCell className={styles.cell}>Seed types</TableHeaderCell>
                <TableHeaderCell className={styles.cell}>Pieces</TableHeaderCell>
                <TableHeaderCell className={styles.cell}>Objectives</TableHeaderCell>
                <TableHeaderCell className={styles.cell}>Harm categories</TableHeaderCell>
                <TableHeaderCell className={styles.cell}>Inspect</TableHeaderCell>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((item, index) => {
                const rowKey = keys[index] ?? item.example_id
                const selected = choice?.exampleId === item.example_id
                const label = item.name || item.preview || item.example_id
                return (
                  <TableRow key={rowKey} aria-selected={selected}>
                    <TableCell className={styles.cell}>
                      <Text weight="semibold" className={styles.preview}>{item.name ?? 'Unnamed example'}</Text>
                      <p className={styles.preview}>{item.preview}</p>
                      {item.preview_truncated ? <Text size={200}>Preview shortened</Text> : null}
                    </TableCell>
                    <TableCell className={styles.cell}>
                      <ValueBadges values={item.modalities} />
                    </TableCell>
                    <TableCell className={styles.cell}>
                      <ValueBadges values={item.seed_types} />
                    </TableCell>
                    <TableCell className={styles.cell}>{item.piece_count.toLocaleString('en-US')}</TableCell>
                    <TableCell className={styles.cell}>{item.objective_count.toLocaleString('en-US')}</TableCell>
                    <TableCell className={styles.cell}>
                      <ValueBadges values={item.harm_categories} unlabeled={item.has_unlabeled_harm} />
                    </TableCell>
                    <TableCell className={styles.cell}>
                      <Button
                        ref={(node) => {
                          if (node) buttonRefs.current.set(rowKey, node)
                          else buttonRefs.current.delete(rowKey)
                        }}
                        className={styles.showButton}
                        appearance={selected ? 'primary' : 'secondary'}
                        aria-pressed={selected}
                        aria-expanded={selected}
                        aria-label={`${selected ? 'Hide' : 'Show'} ${label}`}
                        tabIndex={selected || (!selectedOnPage && index === 0) ? 0 : -1}
                        onClick={() => { selectExample(selected ? null : item.example_id, !selected) }}
                        onKeyDown={(event) => { onExampleKeyDown(event, index) }}
                      >
                        {selected ? 'Hide example' : 'Show example'}
                      </Button>
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </div>
      )}

      {!query.cursorRepeated && !pageState.loading && !pageState.error && pageState.page && pageState.page.total > 0 && (
        <div className={styles.pagination}>
          <Button
            className={styles.touchTarget}
            appearance="subtle"
            icon={<ChevronLeftRegular />}
            disabled={visitedCursors.length === 0}
            onClick={showPrevious}
          >
            Previous page
          </Button>
          <Text>{pageNumber == null ? 'Continued page' : `Page ${pageNumber}`}</Text>
          <Button
            className={styles.touchTarget}
            appearance="subtle"
            icon={<ChevronRightRegular />}
            iconPosition="after"
            disabled={!pageState.page.pagination.has_more || !pageState.page.pagination.next_cursor}
            onClick={showNext}
          >
            Next page
          </Button>
        </div>
      )}

      {query.exampleRepeated && (
        <p className={styles.message}>This link names more than one example. Choose an example from the table.</p>
      )}
      {query.exampleId && detailState.loading && <Spinner label="Loading example..." />}
      {query.exampleId && detailState.error && (
        <div>
          <MessageBar intent="error"><MessageBarBody>{detailState.error.detail}</MessageBarBody></MessageBar>
          <Button ref={detailRetryRef} className={styles.touchTarget} appearance="primary" onClick={detailState.retry}>
            Retry example
          </Button>
        </div>
      )}
      {exampleMissing && (
        <div>
          <p ref={missingExampleRef} tabIndex={-1} className={styles.message}>
            This example is not in the dataset.
          </p>
          <Button
            className={styles.touchTarget}
            appearance="primary"
            onClick={() => { selectExample(null, false) }}
          >
            Back to the examples
          </Button>
        </div>
      )}
      {detailState.detail && query.exampleId === detailState.detail.example_id && (
        <SeedExampleDetail
          detail={detailState.detail}
          headingRef={detailHeadingRef}
          onRefreshUrls={detailState.refreshDisplayUrls}
        />
      )}
    </div>
  )
}

function ValueBadges({ values, unlabeled = false }: { values: readonly string[]; unlabeled?: boolean }) {
  const styles = useSeedExampleBrowserStyles()
  if (values.length === 0 && !unlabeled) return <Text>None</Text>
  return (
    <span className={styles.badges}>
      {values.map((value, index) => (
        <Badge key={`${index}:${value}`} className={styles.valueBadge} appearance="outline">{value}</Badge>
      ))}
      {unlabeled ? <Badge className={styles.valueBadge} appearance="outline">{NOT_LABELED_LABEL}</Badge> : null}
    </span>
  )
}
