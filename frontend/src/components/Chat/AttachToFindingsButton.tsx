import { useRef, useState } from 'react'

import { Button } from '@fluentui/react-components'
import { AttachRegular } from '@fluentui/react-icons'

import { useAttachToFindingsButtonStyles } from './AttachToFindingsButton.styles'
import FindingEvidenceDialog from './FindingEvidenceDialog'

interface AttachToFindingsButtonProps {
  readonly attackResultId: string
  /** The Operation offered first when the attack has no Operation of its own. */
  readonly defaultOperationId?: string | null
  /** Running attacks cannot be attached until they are saved. */
  readonly disabled?: boolean
  /** Called after the attack is attached, such as to refresh `useAttackFindings`. */
  readonly onAttached?: () => void
}

/** Opens the finding picker to attach one attack as evidence, returning focus to the button. */
export default function AttachToFindingsButton({
  attackResultId, defaultOperationId = null, disabled = false, onAttached,
}: AttachToFindingsButtonProps) {
  const styles = useAttachToFindingsButtonStyles()
  const [open, setOpen] = useState(false)
  const trigger = useRef<HTMLButtonElement>(null)
  return (
    <>
      <Button ref={trigger} className={styles.button} icon={<AttachRegular />} disabled={disabled}
        title={disabled ? 'Available after the attack finishes' : 'Attach this attack as evidence to a finding'}
        onClick={() => { setOpen(true) }}>
        Attach to findings
      </Button>
      <FindingEvidenceDialog attackResultId={attackResultId} disabled={disabled} defaultOperationId={defaultOperationId}
        open={open} onOpenChange={setOpen} returnFocusRef={trigger} onAttached={onAttached} />
    </>
  )
}
