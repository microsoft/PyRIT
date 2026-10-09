import { useEffect, useRef, useState } from 'react'
import { Button, Combobox, MessageBar, MessageBarBody, Option } from '@fluentui/react-components'

import OperationCreateDialog from '@/components/Operations/OperationCreateDialog'
import { operationsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { Operation } from '@/types'
import { useLabelsBarStyles } from './LabelsBar.styles'

const MAX_LISTED = 200

interface OperationPickerProps {
  currentValue: string
  onSelect: (name: string) => void
}

export default function OperationPicker({ currentValue, onSelect }: OperationPickerProps) {
  const styles = useLabelsBarStyles()
  const rootRef = useRef<HTMLDivElement>(null)
  const localInputRef = useRef<HTMLInputElement>(null)
  const [open, setOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const [search, setSearch] = useState('')
  const [operations, setOperations] = useState<Operation[]>([])
  const [revision, setRevision] = useState(0)
  const [settledRevision, setSettledRevision] = useState(-1)
  const [error, setError] = useState('')
  const loading = settledRevision !== revision

  useEffect(() => {
    if (!open) return
    let ignore = false
    operationsApi.list()
      .then(result => { if (!ignore) { setOperations(result.items); setError(''); setSettledRevision(revision) } })
      .catch((cause: unknown) => { if (!ignore) { setError(toApiError(cause).detail); setSettledRevision(revision) } })
    return () => { ignore = true }
  }, [revision, open])

  const dismiss = (): void => { setOpen(false); setSearch('') }
  const select = (operation: Operation): void => {
    onSelect(operation.name)
    dismiss()
  }

  const matches = operations.filter(operation => operation.name.toLowerCase().includes(search.toLowerCase()))
  const preferred = matches.find(operation => operation.name.toLowerCase() === search.toLowerCase())
    ?? matches.find(operation => operation.name === currentValue)
  const shown = (preferred ? [preferred, ...matches.filter(operation => operation.id !== preferred.id)] : matches).slice(0, MAX_LISTED)

  return (
    <div className={styles.operationEditor} ref={rootRef} onBlur={event => {
      if (!rootRef.current?.contains(event.relatedTarget)) dismiss()
    }}>
      <Combobox
        ref={localInputRef}
        className={styles.operationPicker}
        size="small"
        freeform
        open={open}
        onOpenChange={(_, data) => {
          setOpen(data.open)
          setSearch('')
          if (data.open) setSettledRevision(-1)
        }}
        value={open ? search : currentValue}
        placeholder={open ? 'Search operations' : 'Select operation'}
        selectedOptions={operations.filter(operation => operation.name === currentValue).map(operation => operation.id)}
        onChange={event => { setSearch(event.target.value); setOpen(true) }}
        onOptionSelect={(_, data) => {
          if (data.optionValue === '--new-operation') {
            dismiss()
            setCreating(true)
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
      {!loading && error && (
        <MessageBar intent="error"><MessageBarBody>
          {error}{' '}
          <Button size="small" className={styles.actionButton} onClick={() => {
            setRevision(value => value + 1)
            localInputRef.current?.focus()
            setOpen(true)
          }}
            aria-label="Retry operations">Retry</Button>
        </MessageBarBody></MessageBar>
      )}
      <OperationCreateDialog open={creating} onOpenChange={setCreating} returnFocusRef={localInputRef}
        onCreated={select} onUseExisting={select} />
    </div>
  )
}
