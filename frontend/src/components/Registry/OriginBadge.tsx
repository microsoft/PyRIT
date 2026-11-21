import { Badge, type BadgeProps } from '@fluentui/react-components'

import type { RegistryEntryOrigin } from '@/types'
import { ORIGIN_LABELS } from '@/utils/registryOrigin'

const ORIGIN_BADGE_COLORS: Record<RegistryEntryOrigin, BadgeProps['color']> = {
  saved: 'success',
  configuration: 'informative',
  temporary: 'warning',
}

interface OriginBadgeProps {
  readonly origin: RegistryEntryOrigin
}

/** Shows whether a registry entry comes back after a restart: Saved, From configuration, or Temporary. */
export default function OriginBadge({ origin }: OriginBadgeProps) {
  return (
    <Badge appearance="tint" color={ORIGIN_BADGE_COLORS[origin]} size="small">
      {ORIGIN_LABELS[origin]}
    </Badge>
  )
}
