import { useEffect, useId, useRef, useState } from 'react'

import {
  Button, Checkbox, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle, DialogTrigger,
  Field, MessageBar, MessageBarBody, Select, Spinner, Text,
} from '@fluentui/react-components'
import { ArrowDownloadRegular } from '@fluentui/react-icons'

import { operationsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { FindingExportFormat, FindingListItem, Operation } from '@/types'
import { downloadFile } from '@/utils/downloadFile'
import { findingExportFilename } from '@/utils/findingExportFilename'

import { useFindingExportDialogStyles } from './FindingExportDialog.styles'
import FindingSeverityBadge from './FindingSeverityBadge'

const PAGE_SIZE = 100
const FORMATS: Record<FindingExportFormat, { option: string; name: string; extension: string }> = {
  docx: { option: 'Word (.docx)', name: 'Word', extension: 'docx' },
  markdown: { option: 'Markdown (.md)', name: 'Markdown', extension: 'md' },
}
const NO_SELECTION: ReadonlySet<string> = new Set()
const NOT_FOUND = 404

type FindingsLoad =
  | { status: 'loading' }
  | { status: 'error'; error: string }
  | { status: 'loaded'; findings: FindingListItem[] }

/** The selection to restore after reloading for a deleted finding, and why the export failed. */
interface SelectionRecovery {
  selected: readonly FindingListItem[]
  detail: string
}

function removedNotice(titles: readonly string[]): string {
  const names = titles.map(title => `"${title}"`).join(', ')
  const verb = titles.length === 1 ? 'no longer exists and was' : 'no longer exist and were'
  return `${names} ${verb} removed from your selection. Review the list and download again.`
}

interface FindingExportDialogProps {
  operation: Operation
}

export default function FindingExportDialog({ operation }: FindingExportDialogProps) {
  const styles = useFindingExportDialogStyles()
  const [open, setOpen] = useState(false)
  const [loadRevision, setLoadRevision] = useState(0)
  const [load, setLoad] = useState<FindingsLoad>({ status: 'loading' })
  const [selected, setSelected] = useState<ReadonlySet<string>>(NO_SELECTION)
  const [format, setFormat] = useState<FindingExportFormat>('docx')
  const [exportError, setExportError] = useState('')
  const [removedTitles, setRemovedTitles] = useState<readonly string[]>([])
  const [recovering, setRecovering] = useState(false)
  const [exporting, setExporting] = useState(false)
  const submitting = useRef(false)
  const recovery = useRef<SelectionRecovery | null>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const rowId = useId()
  // Aborted when the dialog closes or unmounts, so a late export never downloads into a later view.
  const session = useRef<AbortController | null>(null)

  const findings = load.status === 'loaded' ? load.findings : []
  const selectedIds = findings.filter(finding => selected.has(finding.id)).map(finding => finding.id)
  const allSelected = selectedIds.length === findings.length
  const selectAllChecked = allSelected ? true : selectedIds.length === 0 ? false : 'mixed'
  // A natively disabled button drops keyboard focus out of the dialog, so while an export runs, and while the list
  // reloads or shows nothing selected after a deleted finding, Download stays focusable but unavailable.
  const downloadKeepsFocus = exporting || (selectedIds.length === 0 && (recovering || removedTitles.length > 0))

  useEffect(() => {
    if (!open) return
    const controller = new AbortController()
    session.current = controller
    const trigger = triggerRef.current
    return () => {
      controller.abort()
      // Tabster's restore depends on the browser reporting focus loss from the removed dialog; don't rely on it.
      trigger?.focus()
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    let ignore = false
    const loadAllPages = async (): Promise<void> => {
      const loaded = new Map<string, FindingListItem>()
      let offset: number | null = 0
      while (offset !== null) {
        const page = await operationsApi.listFindings(operation.id, { limit: PAGE_SIZE, offset })
        if (ignore) return
        // A finding created while pages load shifts later pages, so a page can repeat an earlier row.
        for (const finding of page.items) {
          if (!loaded.has(finding.id)) loaded.set(finding.id, finding)
        }
        offset = page.has_more ? page.next_offset : null
      }
      setLoad({ status: 'loaded', findings: [...loaded.values()] })
      setRecovering(false)
      const kept = recovery.current
      recovery.current = null
      if (kept === null) {
        setSelected(new Set(loaded.keys()))
        return
      }
      setSelected(new Set(kept.selected.filter(finding => loaded.has(finding.id)).map(finding => finding.id)))
      const removed = kept.selected.filter(finding => !loaded.has(finding.id)).map(finding => finding.title)
      if (removed.length > 0) setRemovedTitles(removed)
      else setExportError(kept.detail)
    }
    loadAllPages().catch((cause: unknown) => {
      if (ignore) return
      setLoad({ status: 'error', error: toApiError(cause).detail })
      setRecovering(false)
    })
    return () => { ignore = true }
  }, [open, loadRevision, operation.id])

  const start = (): void => {
    submitting.current = false
    recovery.current = null
    setRecovering(false)
    setExporting(false)
    setExportError('')
    setRemovedTitles([])
    setFormat('docx')
    setSelected(NO_SELECTION)
    setLoad({ status: 'loading' })
    setOpen(true)
  }

  const toggle = (findingId: string, checked: boolean): void => {
    setSelected(current => {
      const next = new Set(current)
      if (checked) next.add(findingId)
      else next.delete(findingId)
      return next
    })
  }

  const download = async (): Promise<void> => {
    const controller = session.current
    if (controller === null || controller.signal.aborted || submitting.current || selectedIds.length === 0) return
    submitting.current = true
    setExporting(true)
    setExportError('')
    setRemovedTitles([])
    try {
      const file = await operationsApi.exportFindings(operation.id, {
        finding_ids: selectedIds,
        format,
        dashboard_origin: window.location.origin,
      }, controller.signal)
      if (controller.signal.aborted) return
      // The user's local time; a cross-origin deployment may not expose the server's attachment name.
      downloadFile(file, findingExportFilename(operation, FORMATS[format].extension, new Date()))
      setOpen(false)
    } catch (cause: unknown) {
      if (controller.signal.aborted) return
      const error = toApiError(cause)
      if (error.status === NOT_FOUND) {
        // A selected finding (or the Operation) was deleted; reload, then keep whatever is still there.
        recovery.current = {
          selected: findings.filter(finding => selected.has(finding.id)),
          detail: error.detail,
        }
        setRecovering(true)
        setLoad({ status: 'loading' })
        setLoadRevision(value => value + 1)
      } else {
        setExportError(error.detail)
      }
    } finally {
      if (!controller.signal.aborted) {
        submitting.current = false
        setExporting(false)
      }
    }
  }

  const renderFindings = (): React.ReactNode => {
    if (load.status === 'loading') return <Spinner label="Loading findings…" />
    if (load.status === 'error') {
      return (
        <MessageBar className={styles.messageBar} intent="error" aria-live="polite"><MessageBarBody>
          Could not load findings: {load.error}{' '}
          <Button className={styles.button} onClick={() => {
            setLoad({ status: 'loading' })
            setLoadRevision(value => value + 1)
          }}>Retry</Button>
        </MessageBarBody></MessageBar>
      )
    }
    if (findings.length === 0) return <Text>This operation has no findings to export.</Text>
    return (
      <>
        <Checkbox label="Select all" checked={selectAllChecked} disabled={exporting}
          onChange={() => { setSelected(allSelected ? NO_SELECTION : new Set(findings.map(finding => finding.id))) }} />
        <ul className={styles.list}>
          {findings.map(finding => {
            const createdId = `${rowId}-${finding.id}-created`
            return (
              <li key={finding.id} className={styles.row}>
                <Checkbox className={styles.checkbox} label={{ children: finding.title, className: styles.label }}
                  aria-describedby={createdId} checked={selected.has(finding.id)} disabled={exporting}
                  onChange={(_, data) => { toggle(finding.id, data.checked === true) }} />
                <FindingSeverityBadge finding={finding} />
                <Text size={200} className={styles.created}>
                  <time id={createdId} dateTime={finding.created_at}>
                    {new Date(finding.created_at).toLocaleString()}
                  </time>
                </Text>
              </li>
            )
          })}
        </ul>
      </>
    )
  }

  return (
    <Dialog open={open} onOpenChange={(_, data) => {
      if (data.open) start()
      else setOpen(false)
    }}>
      <DialogTrigger disableButtonEnhancement>
        <Button ref={triggerRef} className={styles.button} icon={<ArrowDownloadRegular />}>Export findings</Button>
      </DialogTrigger>
      <DialogSurface>
        <DialogBody>
          <DialogTitle>Export findings</DialogTitle>
          <DialogContent className={styles.content}>
            <MessageBar className={styles.messageBar} intent="warning"><MessageBarBody>
              Findings, attack objectives and score rationales can contain sensitive content. Nothing is redacted
              automatically.
            </MessageBarBody></MessageBar>
            {exportError && <MessageBar className={styles.messageBar} intent="error" aria-live="polite"><MessageBarBody>
              Could not export findings: {exportError}
            </MessageBarBody></MessageBar>}
            {removedTitles.length > 0 && (
              <MessageBar className={styles.messageBar} intent="warning" aria-live="polite"><MessageBarBody>
                {removedNotice(removedTitles)}
              </MessageBarBody></MessageBar>
            )}
            <Field label="Format">
              <Select value={format} disabled={exporting} onChange={event => {
                const value = event.target.value
                if (value in FORMATS) setFormat(value as FindingExportFormat)
              }}>
                {Object.entries(FORMATS).map(([value, { option }]) => <option key={value} value={value}>{option}</option>)}
              </Select>
            </Field>
            {renderFindings()}
          </DialogContent>
          <DialogActions>
            <DialogTrigger disableButtonEnhancement>
              <Button className={styles.button}>Cancel</Button>
            </DialogTrigger>
            <Button appearance="primary" className={styles.button} disabled={selectedIds.length === 0}
              disabledFocusable={downloadKeepsFocus} onClick={() => { void download() }}>
              {exporting ? 'Preparing download…' : `Download ${FORMATS[format].name}`}
            </Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  )
}
