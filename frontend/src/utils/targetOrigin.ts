import type { TargetOrigin } from '@/types'

/** What each target origin is called in the GUI. */
export const TARGET_ORIGIN_LABELS: Record<TargetOrigin, string> = {
  saved: 'Saved',
  configuration: 'From configuration',
  temporary: 'Temporary',
}
