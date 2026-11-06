import { useId, type MouseEvent, type ReactNode } from 'react'

import { Button, TableCell, TableRow, Text, mergeClasses, type TableRowProps } from '@fluentui/react-components'
import { ChevronDownRegular, ChevronRightRegular } from '@fluentui/react-icons'

import { useRegistryIdentityRowStyles } from './RegistryIdentityRow.styles'

interface RegistryIdentityRowProps {
  readonly name: string
  readonly identity: object | null
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
  readonly rowProps?: TableRowProps
}

export default function RegistryIdentityRow({
  name, identity, columns, expanded, onToggle, children, additionalRows, leadingCell,
  nameContent, nameCellClassName, rowClassName, rowTestId, rowProps,
}: RegistryIdentityRowProps) {
  const styles = useRegistryIdentityRowStyles()
  const detailsId = useId()

  return (
    <>
      <TableRow {...rowProps} className={mergeClasses(styles.row, rowClassName)} data-testid={rowTestId}>
        {leadingCell}
        <TableCell className={nameCellClassName}>
          {identity ? <Button appearance="transparent" className={styles.name}
            icon={expanded ? <ChevronDownRegular /> : <ChevronRightRegular />}
            aria-expanded={expanded} aria-controls={detailsId}
            aria-label={`Identity for ${name}`} onClick={(event: MouseEvent<HTMLButtonElement>) => {
              event.stopPropagation()
              onToggle()
            }}>
            {name}
          </Button> : <Text size={200}>{name}</Text>}
          {nameContent}
        </TableCell>
        {children}
      </TableRow>
      {expanded && identity && <TableRow className={styles.row}>
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
