import { useId } from 'react'

import { Badge, Text } from '@fluentui/react-components'
import { Link, useLocation } from 'react-router'

import type { DatasetInfo } from '@/types'
import { chatOriginFromNavigationState, datasetDetailPath } from '@/utils/routeParams'

import { useDatasetCardStyles } from './DatasetCard.styles'

const UNKNOWN_LABEL = 'Unknown'
const NOT_LABELED_LABEL = 'Not labeled'
const NONE_LABEL = 'None'

interface DatasetCardProps {
  dataset: DatasetInfo
  linked: boolean
}

function formatDatasetCount(value: number | null): string {
  if (value == null) return UNKNOWN_LABEL
  return value.toLocaleString('en-US')
}

interface StoredValuesProps {
  values: readonly string[]
  unlabeled?: boolean
}

function StoredValues({ values, unlabeled = false }: StoredValuesProps) {
  const styles = useDatasetCardStyles()
  if (values.length === 0 && !unlabeled) {
    return <Text>{NONE_LABEL}</Text>
  }
  return (
    <div className={styles.badges}>
      {values.map((value, index) => (
        <Badge key={`${index}:${value}`} className={styles.valueBadge} appearance="outline">{value}</Badge>
      ))}
      {unlabeled && (
        <Badge className={styles.valueBadge} appearance="outline">{NOT_LABELED_LABEL}</Badge>
      )}
    </div>
  )
}

function DatasetCounts({ dataset }: Pick<DatasetCardProps, 'dataset'>) {
  const styles = useDatasetCardStyles()
  return <dl className={styles.counts} aria-label="Counts">
    {([
      ['Logical examples', dataset.logical_examples],
      ['Seed pieces', dataset.seed_pieces],
      ['Objectives', dataset.objectives],
    ] as const).map(([label, count]) => <div key={label} className={styles.count}>
      <dt className={styles.countTerm}>{label}</dt>
      <dd className={styles.countValue}>{formatDatasetCount(count)}</dd>
    </div>)}
  </dl>
}

/** Summary card built only from one catalog record. */
export default function DatasetCard({ dataset, linked }: DatasetCardProps) {
  const styles = useDatasetCardStyles()
  const location = useLocation()
  const titleId = useId()
  const descriptionId = useId()
  const catalogPath = `${location.pathname}${location.search}`
  const chatOrigin = chatOriginFromNavigationState(location.state)

  return (
    <article
      className={styles.card}
      aria-labelledby={linked ? titleId : undefined}
      aria-label={linked ? undefined : dataset.name}
    >
      {linked && (
        <h2 id={titleId} className={styles.title}>
          <Link
            to={datasetDetailPath(dataset.selection_key)}
            state={{ ...(chatOrigin ? { chatOrigin } : {}), catalogPath }}
            className={styles.cardLink}
            aria-describedby={descriptionId}
          >
            {dataset.name}
          </Link>
        </h2>
      )}
      {linked && (
        <span id={descriptionId} className={styles.visuallyHidden}>
          {dataset.loaded ? 'Opens this dataset and lists its stored examples.' : 'Opens provider information.'}
        </span>
      )}
      <div className={styles.badges}>
        {dataset.loaded && <Badge appearance="outline">In memory</Badge>}
        {dataset.provider_available && <Badge appearance="outline">Available</Badge>}
      </div>
      {linked && dataset.loaded && <DatasetCounts dataset={dataset} />}
      {dataset.provider && <section className={styles.providerMetadata} aria-label="Provider metadata">
        {!linked && <Text weight="semibold">Default provider</Text>}
        <p className={linked ? styles.descriptionPreview : undefined}>
          {dataset.provider.description ?? 'Description unknown.'}
        </p>
        {dataset.provider.unavailable_reason && <p>{dataset.provider.unavailable_reason}</p>}
        {linked ? <div className={styles.badges}>
          {dataset.provider.modalities.map((modality: string) =>
            <Badge key={modality} appearance="outline">{modality}</Badge>,
          )}
          {dataset.provider.size.length > 0 && <Text size={200}>Size: {dataset.provider.size.join(', ')}</Text>}
          {dataset.provider.load_time.length > 0 && <Text size={200}>Load: {dataset.provider.load_time.join(', ')}</Text>}
        </div> : <dl className={styles.providerFields}>
          {([
            ['Source', dataset.provider.source_url],
            ['License', dataset.provider.license_url],
            ['Access', dataset.provider.access_url],
          ] as const).map(([label, url]) => <div key={label}>
            <dt>{label}:</dt>
            <dd>{url && /^https:\/\//.test(url)
              ? <a href={url} aria-label={`${label}: ${url}`} target="_blank" rel="noopener noreferrer">{url}</a>
              : UNKNOWN_LABEL}</dd>
          </div>)}
          <div><dt>Declared modalities:</dt><dd>{dataset.provider.modalities.join(', ') || UNKNOWN_LABEL}</dd></div>
          <div><dt>Declared harm categories:</dt><dd>{dataset.provider.harm_categories.join(', ') || UNKNOWN_LABEL}</dd></div>
          <div><dt>Approximate size:</dt><dd>{dataset.provider.size.join(', ') || UNKNOWN_LABEL}</dd></div>
          <div><dt>Estimated load time:</dt><dd>{dataset.provider.load_time.join(', ') || UNKNOWN_LABEL}</dd></div>
        </dl>}
        {dataset.provider.required_environment_variables.length > 0 && <p>
          {linked
            ? `May require ${dataset.provider.required_environment_variables.join(', ')}`
            : <>Access may require {dataset.provider.required_environment_variables.join(', ')}.
              Configuring credentials does not establish permission to access the source.</>}
        </p>}
      </section>}
      {!linked && <DatasetCounts dataset={dataset} />}
      {(!linked || dataset.loaded) && <><div className={styles.metaBlock}>
        <Text size={200} weight="semibold" className={styles.metaLabel}>Modalities</Text>
        <StoredValues values={dataset.modalities} />
      </div>
      <div className={styles.metaBlock}>
        <Text size={200} weight="semibold" className={styles.metaLabel}>Harm categories</Text>
        <StoredValues
          values={dataset.harm_categories}
          unlabeled={dataset.has_unlabeled_harm_categories}
        />
      </div></>}
    </article>
  )
}
