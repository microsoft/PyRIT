import { useCallback, type MouseEvent } from 'react'

import { Button, mergeClasses } from '@fluentui/react-components'
import { ChevronDownRegular, ChevronRightRegular } from '@fluentui/react-icons'

import { useIdentityDisclosureStyles } from './IdentityDisclosure.styles'

interface IdentityDisclosureButtonProps {
  readonly name: string
  readonly expanded: boolean
  readonly controlsId: string
  readonly onToggle: () => void
  readonly className?: string
}

/** The component name that reveals its saved identity JSON. */
export function IdentityDisclosureButton({
  name, expanded, controlsId, onToggle, className,
}: IdentityDisclosureButtonProps) {
  const styles = useIdentityDisclosureStyles()
  return (
    <Button
      appearance="transparent"
      className={mergeClasses(styles.toggle, className)}
      icon={expanded ? <ChevronDownRegular /> : <ChevronRightRegular />}
      aria-expanded={expanded}
      aria-controls={controlsId}
      aria-label={`Identity for ${name}`}
      onClick={(event: MouseEvent<HTMLButtonElement>) => {
        // Disclosures sit inside clickable rows; toggling must not also open the row.
        event.stopPropagation()
        onToggle()
      }}
    >
      {name}
    </Button>
  )
}

interface IdentityJsonProps {
  readonly id: string
  readonly name: string
  readonly identity: object
  readonly className?: string
}

/** The saved identity JSON, scrolled into view when it is revealed. */
export function IdentityJson({ id, name, identity, className }: IdentityJsonProps) {
  const styles = useIdentityDisclosureStyles()
  const reveal = useCallback((element: HTMLDivElement | null): void => {
    element?.scrollIntoView({ block: 'nearest', inline: 'start' })
  }, [])
  return (
    <div ref={reveal} id={id} role="region" aria-label={`Identity for ${name}`} className={mergeClasses(styles.details, className)}>
      <pre className={styles.json}>{JSON.stringify(identity, null, 2)}</pre>
    </div>
  )
}
