import { useEffect, useRef } from 'react'

import { Button, MessageBar, MessageBarBody, Spinner, Text } from '@fluentui/react-components'
import { ArrowSyncRegular } from '@fluentui/react-icons'
import { Link, useSearchParams } from 'react-router'

import { useLoadedDatasets } from '@/hooks/useLoadedDatasets'
import { DATASETS_PATH, selectionKeyFromSearchParams } from '@/utils/routeParams'

import DatasetCard from './DatasetCard'
import { useDatasetSelectionStyles } from './DatasetSelection.styles'

const MAX_SHOWN_SELECTION_KEY_LENGTH = 80

function shownSelectionKey(selectionKey: string): string {
  if (selectionKey.length <= MAX_SHOWN_SELECTION_KEY_LENGTH) return selectionKey
  return `${selectionKey.slice(0, MAX_SHOWN_SELECTION_KEY_LENGTH - 1)}…`
}

/**
 * Navigation contract for a later prompt table. The selection key is compared
 * with the catalog response and is not sent to a seed endpoint.
 */
export default function DatasetSelection() {
  const styles = useDatasetSelectionStyles()
  const headingRef = useRef<HTMLHeadingElement>(null)
  const retryRef = useRef<HTMLButtonElement>(null)
  const [searchParams] = useSearchParams()
  const selectionKey = selectionKeyFromSearchParams(searchParams)
  const { datasets, loading, error, retry } = useLoadedDatasets()
  const dataset = selectionKey == null
    ? undefined
    : datasets.find((item) => item.selection_key === selectionKey)

  useEffect(() => {
    if (loading) return
    if (error) {
      retryRef.current?.focus()
      return
    }
    headingRef.current?.focus()
  }, [loading, error, selectionKey, dataset?.selection_key])

  const title = loading
    ? 'Loading dataset'
    : error
      ? 'Could not load datasets'
      : dataset
        ? dataset.name
        : 'Dataset not found'

  return (
    <section className={styles.root} aria-labelledby="dataset-selection-title">
      <Link className={styles.backLink} to={DATASETS_PATH}>Back to datasets</Link>
      <h1 id="dataset-selection-title" ref={headingRef} tabIndex={-1} className={styles.title}>
        {title}
      </h1>
      {loading ? (
        <Spinner label="Loading datasets..." />
      ) : error ? (
        <div className={styles.centeredState}>
          <MessageBar intent="error">
            <MessageBarBody>{error}</MessageBarBody>
          </MessageBar>
          <Button ref={retryRef} appearance="primary" icon={<ArrowSyncRegular />} onClick={retry}>
            Retry
          </Button>
        </div>
      ) : dataset ? (
        <>
          <div className={styles.summary}>
            <DatasetCard dataset={dataset} linked={false} />
          </div>
          <p className={styles.note}>
            Individual examples are not listed in this view.
          </p>
        </>
      ) : (
        <>
          <Text size={300}>No loaded dataset matches this selection.</Text>
          {selectionKey ? <p className={styles.selectionKey}>{shownSelectionKey(selectionKey)}</p> : null}
        </>
      )}
    </section>
  )
}
