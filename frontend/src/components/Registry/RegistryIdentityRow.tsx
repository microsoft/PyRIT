import { useId, type ReactNode } from 'react'

import { Button, TableCell, TableRow, mergeClasses } from '@fluentui/react-components'
import { ChevronDownRegular, ChevronRightRegular } from '@fluentui/react-icons'

import { useRegistryIdentityRowStyles } from './RegistryIdentityRow.styles'

interface RegistryIdentityRowProps {
  readonly name: string
  readonly identity: object
  readonly columns: number
  readonly expanded: boolean
  readonly onToggle: () => void
  readonly children?: ReactNode
  readonly additionalRows?: ReactNode
  readonly leadingCell?: ReactNode
  readonly nameContent?: ReactNode
  readonly nameCellClassName?: string
  readonly rowClassName?: string
  readonly rowTestId?: string
}

export default function RegistryIdentityRow({
  name, identity, columns, expanded, onToggle, children, additionalRows, leadingCell,
  nameContent, nameCellClassName, rowClassName, rowTestId,
}: RegistryIdentityRowProps) {
  const styles = useRegistryIdentityRowStyles()
  const detailsId = useId()

  return (
    <>
      <TableRow className={mergeClasses(styles.row, rowClassName)} data-testid={rowTestId}>
        {leadingCell}
        <TableCell className={nameCellClassName}>
          <Button appearance="transparent" className={styles.name}
            icon={expanded ? <ChevronDownRegular /> : <ChevronRightRegular />}
            aria-expanded={expanded} aria-controls={detailsId}
            aria-label={`Identity for ${name}`} onClick={onToggle}>
            {name}
          </Button>
          {nameContent}
        </TableCell>
        {children}
      </TableRow>
      {expanded && <TableRow className={styles.row}>
        <TableCell colSpan={columns}>
          <div id={detailsId} role="region" aria-label={`Identity for ${name}`} className={styles.details}>
            <pre className={styles.json}>{JSON.stringify(identity, null, 2)}</pre>
          </div>
        </TableCell>
      </TableRow>}
      {additionalRows}
    </>
  )
}
