import type { RegistryRecipeKind, RestoreFailure } from '@/types'

import { useRestoreFailuresStyles } from './RestoreFailures.styles'

const KIND_LABELS: Record<RegistryRecipeKind, string> = {
  targets: 'Target',
  converters: 'Converter',
  scorers: 'Scorer',
  techniques: 'Technique',
}

function describeFailure(failure: RestoreFailure): string {
  const subject = failure.name === null ? `Saved ${failure.kind}` : `${KIND_LABELS[failure.kind]} ${failure.name}`
  // The message usually names the entry it depends on already.
  const dependency = failure.dependency && !failure.message.includes(`'${failure.dependency.name}'`)
    ? ` (${KIND_LABELS[failure.dependency.kind].toLowerCase()} ${failure.dependency.name})`
    : ''
  return `${subject}: ${failure.message}${dependency}`
}

interface RestoreFailuresProps {
  failures: RestoreFailure[]
}

export default function RestoreFailures({ failures }: RestoreFailuresProps) {
  const styles = useRestoreFailuresStyles()
  return (
    <div
      role="region"
      aria-label="Saved entries that couldn't be restored"
      // Focusable so a long list can be scrolled from the keyboard.
      tabIndex={0}
      className={styles.list}
    >
      <ul>
        {failures.map((failure) => (
          <li key={`${failure.kind}/${failure.name ?? ''}`}>{describeFailure(failure)}</li>
        ))}
      </ul>
    </div>
  )
}
