import { useEffect, useId, useMemo, useRef } from 'react'

import {
  Button,
  Checkbox,
  Input,
  Select,
  Spinner,
  Text,
  MessageBar,
  MessageBarBody,
} from '@fluentui/react-components'
import { ArrowSyncRegular, SearchRegular } from '@fluentui/react-icons'
import { useSearchParams } from 'react-router'

import SearchableMultiCombobox from '@/components/SearchableMultiCombobox'
import { useLoadedDatasets } from '@/hooks/useLoadedDatasets'
import type { DatasetInfo, FilterOption } from '@/types'

import DatasetCard from './DatasetCard'
import { useDatasetCatalogStyles } from './DatasetCatalog.styles'
import {
  catalogQueryFromSearchParams,
  catalogQueryIsDefault,
  catalogQueryToSearchParams,
  DATASET_SORT_LABELS,
  DATASET_SORTS,
  DEFAULT_DATASET_CATALOG_QUERY,
  filterDatasets,
  isDatasetSort,
  type DatasetCatalogQuery,
} from './datasetQuery'

function uniqueLabels(values: readonly string[]): FilterOption[] {
  const seen = new Set<string>()
  const options: FilterOption[] = []
  for (const value of values) {
    if (seen.has(value)) continue
    seen.add(value)
    options.push({ value, label: value })
  }
  options.sort((left, right) => left.label.localeCompare(right.label, 'en', { sensitivity: 'base' }))
  return options
}

function modalityOptions(datasets: readonly DatasetInfo[]): FilterOption[] {
  const values: string[] = []
  for (const dataset of datasets) values.push(...dataset.modalities)
  return uniqueLabels(values)
}

function harmOptions(datasets: readonly DatasetInfo[]): FilterOption[] {
  const values: string[] = []
  for (const dataset of datasets) values.push(...dataset.harm_categories)
  return uniqueLabels(values)
}

/** Loaded-dataset catalog. Search, facets, and sort stay in the URL. */
export default function DatasetCatalog() {
  const styles = useDatasetCatalogStyles()
  const titleId = useId()
  const retryRef = useRef<HTMLButtonElement>(null)
  const [searchParams, setSearchParams] = useSearchParams()
  const catalogSearch = searchParams.toString()
  const query = useMemo(
    () => catalogQueryFromSearchParams(new URLSearchParams(catalogSearch)),
    [catalogSearch],
  )
  const { datasets, loading, error, retry } = useLoadedDatasets()

  useEffect(() => {
    if (error) retryRef.current?.focus()
  }, [error])

  const filtered = useMemo(() => filterDatasets(datasets, query), [datasets, query])
  const modalities = useMemo(() => modalityOptions(datasets), [datasets])
  const harms = useMemo(() => harmOptions(datasets), [datasets])
  const filtersActive = !catalogQueryIsDefault(query)

  const updateQuery = (next: DatasetCatalogQuery): void => {
    setSearchParams(catalogQueryToSearchParams(next), { replace: true })
  }

  const resultLabel = filtered.length === 1 ? '1 dataset' : `${filtered.length} datasets`

  return (
    <section className={styles.root} aria-labelledby={titleId} data-testid="dataset-catalog">
      <div className={styles.header}>
        <div className={styles.headerText}>
          <Text id={titleId} as="h1" size={600} weight="semibold">Datasets</Text>
          <Text size={300} className={styles.subtitle}>
            Loaded datasets in memory. Search, filters, and sort stay in the address bar.
            This view does not download datasets or list individual examples.
          </Text>
        </div>
        <Button
          className={styles.touchTarget}
          appearance="subtle"
          icon={<ArrowSyncRegular />}
          onClick={retry}
          disabled={loading}
        >
          Refresh
        </Button>
      </div>

      <div className={styles.filters}>
        <Input
          className={styles.search}
          contentBefore={<SearchRegular />}
          placeholder="Search datasets by name"
          value={query.search}
          onChange={(_, data) => updateQuery({ ...query, search: data.value })}
          aria-label="Search datasets"
        />
        <SearchableMultiCombobox
          className={styles.filterControl}
          ariaLabel="Modalities"
          placeholder="All modalities"
          summaryPrefix="Modalities"
          options={modalities}
          selectedOptions={[...query.modalities]}
          onSelect={(selected) => updateQuery({ ...query, modalities: selected })}
          testId="dataset-modality-filter"
        />
        <SearchableMultiCombobox
          className={styles.filterControl}
          ariaLabel="Harm categories"
          placeholder="All harm categories"
          summaryPrefix="Harm categories"
          options={harms}
          selectedOptions={[...query.harmCategories]}
          onSelect={(selected) => updateQuery({ ...query, harmCategories: selected })}
          testId="dataset-harm-filter"
        />
        <Checkbox
          checked={query.unlabeled}
          onChange={(_, data) => updateQuery({ ...query, unlabeled: data.checked === true })}
          label="Not labeled"
        />
        <Select
          className={styles.filterControl}
          aria-label="Sort datasets"
          value={query.sort}
          onChange={(_, data) => updateQuery({
            ...query,
            sort: isDatasetSort(data.value) ? data.value : DEFAULT_DATASET_CATALOG_QUERY.sort,
          })}
        >
          {DATASET_SORTS.map((sort) => (
            <option key={sort} value={sort}>{DATASET_SORT_LABELS[sort]}</option>
          ))}
        </Select>
        {filtersActive && (
          <Button className={styles.touchTarget} appearance="subtle" onClick={() => updateQuery(DEFAULT_DATASET_CATALOG_QUERY)}>
            Clear filters
          </Button>
        )}
      </div>

      {loading ? (
        <div className={styles.centeredState}>
          <Spinner label="Loading datasets..." />
        </div>
      ) : error ? (
        <div className={styles.centeredState}>
          <MessageBar intent="error">
            <MessageBarBody>{error}</MessageBarBody>
          </MessageBar>
          <Button
            ref={retryRef}
            className={styles.touchTarget}
            appearance="primary"
            icon={<ArrowSyncRegular />}
            onClick={retry}
          >
            Retry
          </Button>
        </div>
      ) : datasets.length === 0 ? (
        <div className={styles.centeredState}>
          <Text size={400}>No datasets in memory</Text>
          <Text size={200}>
            Datasets show up here after they are loaded into memory. This view does not download them.
          </Text>
        </div>
      ) : filtered.length === 0 ? (
        <div className={styles.centeredState}>
          <Text size={400}>
            {query.search.trim()
              ? `No datasets match "${query.search.trim()}"`
              : 'No datasets match these filters'}
          </Text>
          <Text size={200}>Clear the search or filters to see every loaded dataset.</Text>
        </div>
      ) : (
        <>
          <Text className={styles.resultCount} role="status" aria-live="polite">{resultLabel}</Text>
          <ul className={styles.grid} aria-label="Datasets">
            {filtered.map((dataset) => (
              <li key={dataset.selection_key}>
                <DatasetCard dataset={dataset} linked headingLevel="h2" />
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}
