import { Badge, type BadgeProps } from '@fluentui/react-components'

import type { TargetOrigin } from '@/types'
import { TARGET_ORIGIN_LABELS } from '@/utils/targetOrigin'

const ORIGIN_BADGE_COLORS: Record<TargetOrigin, BadgeProps['color']> = {
  saved: 'success',
  configuration: 'informative',
  temporary: 'warning',
}

interface TargetOriginBadgeProps {
  readonly origin: TargetOrigin
}

/** Shows whether a target comes back after a restart: Saved, From configuration, or Temporary. */
export default function TargetOriginBadge({ origin }: TargetOriginBadgeProps) {
  return (
    <Badge appearance="tint" color={ORIGIN_BADGE_COLORS[origin]} size="small">
      {TARGET_ORIGIN_LABELS[origin]}
    </Badge>
  )
}
