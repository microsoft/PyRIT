import { Badge } from '@fluentui/react-components'

import type { FindingFields } from '@/types'
import { severityBadge } from '@/utils/findingFields'
import { useFindingSeverityBadgeStyles } from './FindingSeverityBadge.styles'

interface FindingSeverityBadgeProps {
  fields: FindingFields
  severity: string
}

export default function FindingSeverityBadge({ fields, severity }: FindingSeverityBadgeProps) {
  const styles = useFindingSeverityBadgeStyles()
  const { label, color } = severityBadge(fields, severity)
  return <Badge className={styles.badge} appearance="tint" color={color}>{label}</Badge>
}
