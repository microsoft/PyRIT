import type { RegistryEntryOrigin } from '@/types'

/** What each registry entry origin is called in the GUI. */
export const ORIGIN_LABELS: Record<RegistryEntryOrigin, string> = {
  saved: 'Saved',
  configuration: 'From configuration',
  temporary: 'Temporary',
}

/** Who saved a registry entry, or null when nobody signed in saved it. */
export function savedByText(entry: { readonly saved_by?: string | null, readonly saved_by_me?: boolean }): string | null {
  if (entry.saved_by_me) return 'Saved by you'
  return entry.saved_by ? `Saved by ${entry.saved_by}` : null
}
