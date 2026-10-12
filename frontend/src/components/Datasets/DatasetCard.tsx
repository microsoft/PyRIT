import { useId } from 'react'

import { Badge, Text } from '@fluentui/react-components'
import { Link, useLocation } from 'react-router'

import type { DatasetInfo } from '@/types'
import { datasetDetailPath } from '@/utils/routeParams'

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

/** Summary card built only from one catalog record. */
export default function DatasetCard({ dataset, linked }: DatasetCardProps) {
  const styles = useDatasetCardStyles()
  const location = useLocation()
  const titleId = useId()
  const descriptionId = useId()
  const catalogPath = `${location.pathname}${location.search}`

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
            state={{ catalogPath }}
            className={styles.cardLink}
            aria-describedby={descriptionId}
          >
            {dataset.name}
          </Link>
        </h2>
      )}
      {linked && (
        <span id={descriptionId} className={styles.visuallyHidden}>
          Opens this dataset. Individual examples are not yet listed in this view. We are still building this experience.
        </span>
      )}
      <dl className={styles.counts} aria-label="Counts">
        <div className={styles.count}>
          <dt className={styles.countTerm}>Logical examples</dt>
          <dd className={styles.countValue}>{formatDatasetCount(dataset.logical_examples)}</dd>
        </div>
        <div className={styles.count}>
          <dt className={styles.countTerm}>Seed pieces</dt>
          <dd className={styles.countValue}>{formatDatasetCount(dataset.seed_pieces)}</dd>
        </div>
        <div className={styles.count}>
          <dt className={styles.countTerm}>Objectives</dt>
          <dd className={styles.countValue}>{formatDatasetCount(dataset.objectives)}</dd>
        </div>
      </dl>
      <div className={styles.metaBlock}>
        <Text size={200} weight="semibold" className={styles.metaLabel}>Modalities</Text>
        <StoredValues values={dataset.modalities} />
      </div>
      <div className={styles.metaBlock}>
        <Text size={200} weight="semibold" className={styles.metaLabel}>Harm categories</Text>
        <StoredValues
          values={dataset.harm_categories}
          unlabeled={dataset.has_unlabeled_harm_categories}
        />
      </div>
    </article>
  )
}
