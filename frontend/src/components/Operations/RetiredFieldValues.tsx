import { type RefObject, useEffect, useId, useRef, useState, type JSX } from 'react'
import {
  Button, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle, Link, Text,
  useRestoreFocusTarget,
} from '@fluentui/react-components'

import { useRetiredFieldValuesStyles } from './RetiredFieldValues.styles'

interface RetiredFieldValuesProps {
  /** Retired values from the stored finding, in stored order. */
  values: Array<{ key: string; value: string }>
  /** Keys marked for removal in this edit. */
  removed: ReadonlySet<string>
  disabled?: boolean
  onRemove: (key: string) => void
  /** Restores every marked key. */
  onUndo: () => void
}

function removalNote(count: number): string {
  return count === 1
    ? '1 retired value is removed when you save.'
    : `${count} retired values are removed when you save.`
}

/**
 * Lists stored values whose keys the active finding fields no longer define. They are read-only, but each
 * can be marked for removal on save, after a confirmation, and Undo restores every marked value.
 */
export default function RetiredFieldValues({
  values, removed, disabled = false, onRemove, onUndo,
}: RetiredFieldValuesProps): JSX.Element | null {
  const styles = useRetiredFieldValuesStyles()
  const headingId = useId()
  // The key stays set while the dialog closes, so its title doesn't lose the key during the exit animation.
  const [confirming, setConfirming] = useState({ key: '', open: false })
  // The confirmation dialog opens from state rather than a DialogTrigger. Mark each Remove button so Tabster
  // can return focus to it when the dialog is cancelled.
  const restoreFocusTarget = useRestoreFocusTarget()
  const undoRef = useRef<HTMLButtonElement>(null)
  const firstRemoveRef = useRef<HTMLButtonElement>(null)
  const focusAfterChange = useRef<RefObject<HTMLButtonElement | null> | null>(null)

  // Confirming a removal unmounts the row whose button opened the dialog, and Undo unmounts itself, so focus
  // moves to the control that replaces them once the parent has applied the change.
  useEffect(() => {
    focusAfterChange.current?.current?.focus()
    focusAfterChange.current = null
  }, [removed])

  if (values.length === 0) return null
  const kept = values.filter(({ key }) => !removed.has(key))
  const closeConfirmation = (): void => {
    setConfirming((current) => ({ ...current, open: false }))
  }

  return (
    <div className={styles.root}>
      {kept.length > 0 && <div role="group" aria-labelledby={headingId} className={styles.group}>
        <Text id={headingId} className={styles.heading}>Retired fields · read-only</Text>
        <ul className={styles.list}>
          {kept.map(({ key, value }, index) => (
            <li key={key} className={styles.item}>
              <div className={styles.entry}>
                <code className={styles.key} translate="no">{key}</code>
                <Text as="p" className={styles.value}>{value}</Text>
              </div>
              <Button {...restoreFocusTarget} ref={index === 0 ? firstRemoveRef : undefined} size="small"
                className={styles.button} disabled={disabled} aria-label={`Remove retired value ${key}`}
                onClick={() => { setConfirming({ key, open: true }) }}>Remove</Button>
            </li>
          ))}
        </ul>
      </div>}
      {removed.size > 0 && <div className={styles.note}>
        <Text>{removalNote(removed.size)}</Text>
        <Link ref={undoRef} as="button" className={styles.button} disabled={disabled} onClick={() => {
          focusAfterChange.current = firstRemoveRef
          onUndo()
        }}>Undo</Link>
      </div>}
      <Dialog open={confirming.open} onOpenChange={(_, data) => {
        if (!data.open) closeConfirmation()
      }}>
        <DialogSurface>
          <DialogBody>
            <DialogTitle>Remove <code className={styles.titleKey} translate="no">{confirming.key}</code>?</DialogTitle>
            <DialogContent>
              <Text>
                This deletes its text from the finding when you save. Copy anything you need first. Cancel on the
                edit dialog still brings it back.
              </Text>
            </DialogContent>
            <DialogActions>
              <Button className={styles.button} onClick={closeConfirmation}>Keep it</Button>
              <Button appearance="primary" className={styles.button} disabled={disabled} onClick={() => {
                focusAfterChange.current = undoRef
                onRemove(confirming.key)
                closeConfirmation()
              }}>Remove</Button>
            </DialogActions>
          </DialogBody>
        </DialogSurface>
      </Dialog>
    </div>
  )
}
