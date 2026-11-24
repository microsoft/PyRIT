import { useId } from 'react'
import { mergeClasses, Text } from '@fluentui/react-components'

import type { FindingListItem, FindingValueRow } from '@/types'

import { useOperationsStyles } from './Operations.styles'

interface FindingCardContentProps {
  finding: Pick<FindingListItem, 'rows' | 'retired'>
}

/** A finding card's filled values, then the values of fields that are no longer configured under their raw keys. */
export default function FindingCardContent({ finding }: FindingCardContentProps) {
  const styles = useOperationsStyles()
  const retiredHeadingId = useId()
  const { rows, retired } = finding
  if (rows.length === 0 && retired.length === 0) return null

  return (
    <div className={styles.values}>
      {rows.length > 0 && <dl className={styles.valueList}>
        {rows.map((row: FindingValueRow) => row.multiline ? (
          <div key={row.key}>
            <dt><Text block weight="semibold">{row.label}</Text></dt>
            <dd className={mergeClasses(styles.description, styles.multilineValue)}>{row.value}</dd>
          </div>
        ) : (
          <div key={row.key}>
            <dt className={styles.inlineTerm}><Text>{row.label}:</Text></dt>{' '}
            <dd className={styles.inlineValue}><Text>{row.value}</Text></dd>
          </div>
        ))}
      </dl>}
      {retired.length > 0 && <div role="group" aria-labelledby={retiredHeadingId} className={styles.retired}>
        <Text id={retiredHeadingId} size={200} weight="semibold" className={styles.muted}>Retired fields</Text>
        <dl className={styles.valueList}>
          {retired.map(([key, value]: [string, string]) => (
            <div key={key}>
              <dt><code className={styles.retiredKey} translate="no">{key}</code></dt>
              <dd className={styles.retiredValue}>{value}</dd>
            </div>
          ))}
        </dl>
      </div>}
    </div>
  )
}
