import type { ReactNode } from 'react'

import { Text, mergeClasses } from '@fluentui/react-components'

import { useDetailFactsStyles } from './DetailFacts.styles'

interface DetailFactsProps {
  /** Accessible name of the list, when its heading is not visible. */
  readonly label?: string
  readonly className?: string
  readonly children: ReactNode
}

/** An aligned label/value list that stacks labels above values on narrow viewports. */
export default function DetailFacts({ label, className, children }: DetailFactsProps) {
  const styles = useDetailFactsStyles()
  return <dl aria-label={label} className={mergeClasses(styles.facts, className)}>{children}</dl>
}

interface DetailFactProps {
  readonly label: string
  /** Primary facts are the subject of the list; secondary facts describe it. */
  readonly tone?: 'primary' | 'secondary'
  readonly valueClassName?: string
  readonly children: ReactNode
}

// Fluent Text's `as` does not accept dt/dd, so the native elements wrap Text.
export function DetailFact({ label, tone = 'secondary', valueClassName, children }: DetailFactProps) {
  const styles = useDetailFactsStyles()
  return (
    <>
      <dt className={tone === 'primary' ? styles.primaryLabel : styles.secondaryLabel}>
        <Text weight={tone === 'primary' ? 'semibold' : 'regular'}>{label}</Text>
      </dt>
      <dd className={mergeClasses(styles.value, valueClassName)}>{children}</dd>
    </>
  )
}
