import type { RegistryEntryOrigin } from '@/types'

/** What each registry entry origin is called in the GUI. */
export const ORIGIN_LABELS: Record<RegistryEntryOrigin, string> = {
  saved: 'Saved',
  configuration: 'From configuration',
  temporary: 'Temporary',
}
