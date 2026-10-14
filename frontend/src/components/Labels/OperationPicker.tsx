import { useEffect, useRef, useState } from 'react'
import { Button, Combobox, MessageBar, MessageBarBody, mergeClasses, Option, Text, useId } from '@fluentui/react-components'
import { DismissRegular } from '@fluentui/react-icons'

import OperationCreateDialog from '@/components/Operations/OperationCreateDialog'
import type { OperationSelectionState } from '@/hooks/useOperationSelection'
import { operationsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { Operation } from '@/types'
import { useLabelsBarStyles } from './LabelsBar.styles'

const MAX_LISTED = 200

interface OperationPickerProps {
  selection: OperationSelectionState
  onSelect: (operation: Operation | null) => void
  onRetry: () => void
}

export default function OperationPicker({ selection, onSelect, onRetry }: OperationPickerProps) {
  const styles = useLabelsBarStyles()
  const unregisteredId = useId('operation-unregistered')
  const rootRef = useRef<HTMLDivElement>(null)
  const localInputRef = useRef<HTMLInputElement>(null)
  const [open, setOpen] = useState(false)
  // The dialog remounts only when it opens, so it starts from the right name and still returns focus on close.
  const [creating, setCreating] = useState({ open: false, name: '', key: 0 })
  const [search, setSearch] = useState('')
  const [operations, setOperations] = useState<Operation[]>([])
  const [revision, setRevision] = useState(0)
  const [settledRevision, setSettledRevision] = useState(-1)
  const [error, setError] = useState('')
  const loading = settledRevision !== revision
  const selectedId = selection.status === 'resolved' ? selection.operation.id : null
  const displayName = selection.status === 'resolved' ? selection.operation.name
    : selection.status === 'unregistered' ? selection.name : ''

  useEffect(() => {
    if (!open) return
    let ignore = false
    operationsApi.list()
      .then(result => { if (!ignore) { setOperations(result.items); setError(''); setSettledRevision(revision) } })
      .catch((cause: unknown) => { if (!ignore) { setError(toApiError(cause).detail); setSettledRevision(revision) } })
    return () => { ignore = true }
  }, [revision, open])

  const openList = (): void => {
    if (!open) setSettledRevision(-1)
    setOpen(true)
    setSearch('')
  }
  const dismiss = (): void => { setOpen(false); setSearch('') }
  const select = (operation: Operation): void => {
    onSelect(operation)
    dismiss()
  }
  const startCreate = (name: string): void => {
    dismiss()
    setCreating(current => ({ open: true, name, key: current.key + 1 }))
  }

  const matches = operations.filter(operation => operation.name.toLowerCase().includes(search.toLowerCase()))
  const preferred = matches.find(operation => operation.name.toLowerCase() === search.toLowerCase())
    ?? matches.find(operation => operation.id === selectedId)
  const shown = (preferred ? [preferred, ...matches.filter(operation => operation.id !== preferred.id)] : matches).slice(0, MAX_LISTED)

  return (
    <div className={styles.operationEditor} ref={rootRef} onBlur={event => {
      if (!rootRef.current?.contains(event.relatedTarget)) dismiss()
    }}>
      <div className={styles.operationStatus}>
        <Combobox
          ref={localInputRef}
          className={mergeClasses(styles.operationPicker,
            selection.status === 'unregistered' ? styles.metadataPlaceholder : undefined)}
          aria-describedby={selection.status === 'unregistered' ? unregisteredId : undefined}
          size="small"
          freeform
          open={open}
          onOpenChange={(_, data) => { if (data.open) openList(); else dismiss() }}
          value={open ? search : displayName}
          placeholder={open ? 'Search operations' : selection.status === 'loading' ? 'Loading operation...' : 'Select operation'}
          selectedOptions={selectedId ? [selectedId] : []}
          onChange={event => {
            if (!open) openList()
            setSearch(event.target.value)
          }}
          onOptionSelect={(_, data) => {
            if (data.optionValue === '--new-operation') {
              startCreate('')
              return
            }
            const selected = operations.find(operation => operation.id === data.optionValue)
            if (selected && !loading && !error) select(selected)
          }}
          onKeyDownCapture={event => { if (event.key === 'Tab') event.stopPropagation() }}
          onKeyDown={event => { if (event.key === 'Escape') dismiss() }}
          positioning={{ matchTargetSize: undefined, autoSize: 'width' }}
          listbox={{ className: styles.operationListbox }}
          aria-label="Operation"
          data-testid="edit-label-operation"
        >
          <Option value="--new-operation">New operation…</Option>
          {loading ? (
            <Option disabled value="--loading" className={styles.operationNote}>Loading operations...</Option>
          ) : error ? (
            <Option disabled value="--failed" className={styles.operationNoteError}>Could not load saved operations</Option>
          ) : <>
            {shown.map(operation => <Option key={operation.id} value={operation.id}>{operation.name}</Option>)}
            {operations.length > 0 && matches.length === 0 && (
              <Option disabled value="--empty" className={styles.operationNote}>
                No matching saved operations.
              </Option>
            )}
            {matches.length > MAX_LISTED && (
              <Option disabled value="--more" className={styles.operationNote}>
                {`Showing ${MAX_LISTED} of ${matches.length} — type to narrow`}
              </Option>
            )}
          </>}
        </Combobox>
        {selection.status === 'resolved' && (
          <Button appearance="transparent" size="small" className={styles.removeBtn}
            icon={<DismissRegular fontSize={12} />} aria-label="Clear operation"
            data-testid="remove-label-operation" onClick={() => onSelect(null)} />
        )}
        {selection.status === 'unregistered' && <>
          <span id={unregisteredId} className={styles.srOnly}>Not registered</span>
          <Button size="small" className={styles.actionButton} onClick={() => startCreate(selection.name)}>Register</Button>
        </>}
        {selection.status === 'missing' && <Text size={200} className={styles.operationNoteError}>Saved operation not found</Text>}
        {(selection.status === 'unregistered' || selection.status === 'missing') && (
          <Button size="small" className={styles.actionButton} onClick={() => onSelect(null)}>Clear</Button>
        )}
      </div>
      {selection.status === 'error' && (
        <MessageBar intent="error"><MessageBarBody>
          Could not load the selected operation: {selection.error}{' '}
          <Button size="small" className={styles.actionButton} onClick={onRetry}>Retry</Button>
          <Button size="small" className={styles.actionButton} onClick={() => onSelect(null)}>Clear</Button>
        </MessageBarBody></MessageBar>
      )}
      {!loading && error && (
        <MessageBar intent="error"><MessageBarBody>
          {error}{' '}
          <Button size="small" className={styles.actionButton} onClick={() => {
            setRevision(value => value + 1)
            localInputRef.current?.focus()
            openList()
          }}
            aria-label="Retry operations">Retry</Button>
        </MessageBarBody></MessageBar>
      )}
      <OperationCreateDialog key={creating.key} initialName={creating.name} open={creating.open}
        onOpenChange={value => setCreating(current => ({ ...current, open: value }))} returnFocusRef={localInputRef}
        onCreated={select} onUseExisting={select} />
    </div>
  )
}
