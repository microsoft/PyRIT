import { Badge } from '@fluentui/react-components'

import type { FindingCreate } from '@/types'
import { findingSeverityColor, findingSeverityLabel } from '@/utils/findingSeverity'
import { useFindingSeverityBadgeStyles } from './FindingSeverityBadge.styles'

interface FindingSeverityBadgeProps {
  finding: Pick<FindingCreate, 'severity' | 'severity_other'>
}

export default function FindingSeverityBadge({ finding }: FindingSeverityBadgeProps) {
  const styles = useFindingSeverityBadgeStyles()
  return (
    <Badge className={styles.badge} appearance="tint" color={findingSeverityColor(finding.severity)}>
      {findingSeverityLabel(finding)}
    </Badge>
  )
}
