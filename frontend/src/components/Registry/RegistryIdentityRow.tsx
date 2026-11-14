import { useId, type ReactNode } from 'react'

import { TableCell, TableRow, Text, mergeClasses, type TableRowProps } from '@fluentui/react-components'

import { IdentityDisclosureButton, IdentityJson } from './IdentityDisclosure'
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
          {identity ? <IdentityDisclosureButton name={name} expanded={expanded} controlsId={detailsId}
            className={styles.name} onToggle={onToggle} /> : <Text size={200}>{name}</Text>}
          {nameContent}
        </TableCell>
        {children}
      </TableRow>
      {expanded && identity && <TableRow className={styles.row}>
        <TableCell colSpan={columns}>
          <IdentityJson id={detailsId} name={name} identity={identity} />
        </TableCell>
      </TableRow>}
      {additionalRows}
    </>
  )
}
