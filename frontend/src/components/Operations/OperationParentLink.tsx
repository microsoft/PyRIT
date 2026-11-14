import { BriefcaseRegular } from '@fluentui/react-icons'
import { Link } from 'react-router'

import { useOperationParentLinkStyles } from './OperationParentLink.styles'

interface OperationParentLinkProps {
  readonly id?: string | null
  readonly name?: string | null
}

/** The Operation an item belongs to, shown as small parent context above its title. */
export default function OperationParentLink({ id, name }: OperationParentLinkProps) {
  const styles = useOperationParentLinkStyles()
  if (!id) return null
  const label = name || 'View operation'
  return (
    <Link className={styles.link} to={`/operations/${encodeURIComponent(id)}`} aria-label={`Operation: ${label}`}>
      <BriefcaseRegular className={styles.icon} aria-hidden="true" />
      {label}
    </Link>
  )
}
