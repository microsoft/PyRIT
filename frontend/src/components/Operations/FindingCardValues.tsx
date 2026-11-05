import { useId } from 'react'
import { mergeClasses, Text } from '@fluentui/react-components'

import type { FindingFields, FindingListItem } from '@/types'
import { describeFindingValues, type FindingValueRow } from '@/utils/findingFields'

import { useOperationsStyles } from './Operations.styles'

interface FindingCardValuesProps {
  fields: FindingFields
  finding: Pick<FindingListItem, 'severity' | 'field_values'>
}

/** A finding card's filled values, then the values of fields that are no longer configured under their raw keys. */
export default function FindingCardValues({ fields, finding }: FindingCardValuesProps) {
  const styles = useOperationsStyles()
  const retiredHeadingId = useId()
  const { rows, retired } = describeFindingValues(fields, finding.severity, finding.field_values)
  if (rows.length === 0 && retired.length === 0) return null

  return (
    <div className={styles.values}>
      {rows.map((row: FindingValueRow) => row.multiline ? (
        <div key={row.key}>
          <Text block weight="semibold">{row.label}</Text>
          <p className={mergeClasses(styles.description, styles.valueText)}>{row.value}</p>
        </div>
      ) : <Text key={row.key} block>{`${row.label}: ${row.value}`}</Text>)}
      {retired.length > 0 && <div role="group" aria-labelledby={retiredHeadingId} className={styles.retired}>
        <Text id={retiredHeadingId} size={200} weight="semibold" className={styles.muted}>Retired fields</Text>
        <ul className={styles.retiredList}>
          {retired.map(({ key, value }: { key: string; value: string }) => (
            <li key={key}>
              <code className={styles.retiredKey} translate="no">{key}</code>
              <p className={styles.retiredValue}>{value}</p>
            </li>
          ))}
        </ul>
      </div>}
    </div>
  )
}
