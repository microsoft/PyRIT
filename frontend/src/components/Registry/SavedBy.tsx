import { Text } from '@fluentui/react-components'

import { savedByText } from '@/utils/registryOrigin'

import { useSavedByStyles } from './SavedBy.styles'

interface SavedByProps {
  readonly entry: { readonly saved_by?: string | null, readonly saved_by_me?: boolean }
}

/** Who saved a registry entry, shown under its name, or nothing when nobody signed in saved it. */
export default function SavedBy({ entry }: SavedByProps) {
  const styles = useSavedByStyles()
  const text = savedByText(entry)
  return text === null ? null : <Text size={200} className={styles.savedBy}>{text}</Text>
}
