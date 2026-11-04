import { useEffect, useRef } from 'react'

import { MessageBar, MessageBarBody, Spinner, Text } from '@fluentui/react-components'
import { Link, useLocation, useSearchParams } from 'react-router'

import { useLoadedDatasets } from '@/hooks/useLoadedDatasets'
import { catalogPathFromNavigationState, isChatPath, selectionKeyFromSearchParams } from '@/utils/routeParams'

import DatasetCard from './DatasetCard'
import { SeedExampleBrowserView } from './SeedExampleBrowser'
import { useDatasetSelectionStyles } from './DatasetSelection.styles'
import { useSeedExampleBrowserState } from './useSeedExampleBrowserState'

const MAX_SHOWN_SELECTION_KEY_LENGTH = 80

function shownSelectionKey(selectionKey: string): string {
  if (selectionKey.length <= MAX_SHOWN_SELECTION_KEY_LENGTH) return selectionKey
  return `${selectionKey.slice(0, MAX_SHOWN_SELECTION_KEY_LENGTH - 1)}…`
}

interface ChatOrigin {
  path: string
  state?: unknown
  attackId: string | null
  conversationId: string | null
}

function chatOriginFromNavigationState(state: unknown): ChatOrigin | null {
  if (state == null || typeof state !== 'object' || !('chatOrigin' in state)) return null
  const candidate = (state as { chatOrigin?: unknown }).chatOrigin
  if (candidate == null || typeof candidate !== 'object' || !('path' in candidate)) return null
  const origin = candidate as { path?: unknown; state?: unknown; attackId?: unknown; conversationId?: unknown }
  if (typeof origin.path !== 'string' || !isChatPath(origin.path)) return null
  return {
    path: origin.path,
    state: origin.state,
    attackId: typeof origin.attackId === 'string' ? origin.attackId : null,
    conversationId: typeof origin.conversationId === 'string' ? origin.conversationId : null,
  }
}

/**
 * Selected dataset summary plus a read-only example table.
 * A missing or repeated selection key does not choose a dataset or request seeds.
 */
export default function DatasetSelection() {
  const styles = useDatasetSelectionStyles()
  const headingRef = useRef<HTMLHeadingElement>(null)
  const location = useLocation()
  const [searchParams] = useSearchParams()
  const selectionKey = selectionKeyFromSearchParams(searchParams)
  const catalogPath = catalogPathFromNavigationState(location.state)
  const chatOrigin = chatOriginFromNavigationState(location.state)
  // Stay mounted while a catalog reload unmounts the table, so a runtime change
  // is still compared with the generation that owned the current page.
  const seedBrowser = useSeedExampleBrowserState(selectionKey ?? '')
  const { datasets, loading, error, refresh } = useLoadedDatasets()
  const dataset = selectionKey == null
    ? undefined
    : datasets.find((item) => item.selection_key === selectionKey)

  useEffect(() => {
    if (loading) return
    headingRef.current?.focus()
  }, [loading, selectionKey, dataset?.selection_key])

  const title = loading
    ? 'Loading dataset'
    : dataset
        ? dataset.name
        : error ? 'Datasets' : 'Dataset not found'

  return (
    <section className={styles.root} aria-labelledby="dataset-selection-title">
      <Link className={styles.backLink} to={catalogPath}>Back to datasets</Link>
      {chatOrigin && <Link className={styles.backLink} to={chatOrigin.path} state={chatOrigin.state}>Return to Chat</Link>}
      <h1 id="dataset-selection-title" ref={headingRef} tabIndex={-1} className={styles.title}>
        {title}
      </h1>
      {error && <MessageBar intent="error"><MessageBarBody>{error}</MessageBarBody></MessageBar>}
      {loading ? (
        <Spinner label="Loading datasets..." />
      ) : dataset ? (
        <>
          <div className={styles.summary}>
            <DatasetCard dataset={dataset} linked={false} />
          </div>
          <div className={styles.browser}>
            <SeedExampleBrowserView
              dataset={dataset}
              onCatalogStale={refresh}
              browserState={seedBrowser}
              activeAttackId={chatOrigin?.attackId ?? null}
              activeConversationId={chatOrigin?.conversationId ?? null}
            />
          </div>
        </>
      ) : !error && (
        <>
          <Text size={300}>No loaded dataset matches this selection.</Text>
          {selectionKey ? <p className={styles.selectionKey}>{shownSelectionKey(selectionKey)}</p> : null}
        </>
      )}
    </section>
  )
}
