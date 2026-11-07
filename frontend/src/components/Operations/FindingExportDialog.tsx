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
import { findingChildren, findingDescendantIds, flattenFindingTree } from '@/utils/findingTree'

import { useFindingExportDialogStyles } from './FindingExportDialog.styles'
import FindingSeverityBadge from './FindingSeverityBadge'
import { MAX_EXPORT_FINDINGS } from './findingExportLimits'

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
  const children = findingChildren(findings)
  const roots = children.get(null) ?? []
  // A selected finding includes all of its sub-findings, so they show as checked and cannot be unchecked alone.
  const implied = new Set(findings.filter(finding => selected.has(finding.id))
    .flatMap(finding => findingDescendantIds(children, finding.id)))
  const included = findings.filter(finding => selected.has(finding.id) || implied.has(finding.id))
  const selectedIds = flattenFindingTree(findings)
    .map(({ finding }) => finding.id)
    .filter(findingId => selected.has(findingId) && !implied.has(findingId))
  const allSelected = included.length === findings.length
  const selectAllChecked = allSelected ? true : included.length === 0 ? false : 'mixed'
  const tooMany = included.length > MAX_EXPORT_FINDINGS
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
      const seenOffsets = new Set<number>()
      let offset: number | null = 0
      while (offset !== null) {
        // A repeated offset means the server is not advancing the page, and
        // fetching it again would loop forever, so stop with an error the
        // same way the shared pagination helper treats a cyclic cursor.
        if (seenOffsets.has(offset)) {
          throw new Error('The findings page offset did not advance.')
        }
        seenOffsets.add(offset)
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
        setSelected(new Set((findingChildren([...loaded.values()]).get(null) ?? []).map(finding => finding.id)))
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
      if (checked) {
        next.add(findingId)
        // Its sub-findings are now included with it, so they no longer need their own selection.
        for (const descendant of findingDescendantIds(children, findingId)) next.delete(descendant)
      } else {
        next.delete(findingId)
      }
      return next
    })
  }

  const download = async (): Promise<void> => {
    const controller = session.current
    if (controller === null || controller.signal.aborted || submitting.current || selectedIds.length === 0 || tooMany) {
      return
    }
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
    const renderRows = (rows: readonly FindingListItem[], parentTitle?: string): React.ReactNode => (
      <ul className={parentTitle === undefined ? styles.list : styles.subList}>
        {rows.map(finding => {
          const createdId = `${rowId}-${finding.id}-created`
          const includedId = `${rowId}-${finding.id}-included`
          const isImplied = implied.has(finding.id)
          const subFindings = children.get(finding.id) ?? []
          return (
            <li key={finding.id}>
              <div className={styles.row}>
                <Checkbox className={styles.checkbox} label={{ children: finding.title, className: styles.label }}
                  aria-describedby={isImplied ? `${includedId} ${createdId}` : createdId}
                  checked={selected.has(finding.id) || isImplied} disabled={exporting || isImplied}
                  onChange={(_, data) => { toggle(finding.id, data.checked === true) }} />
                <FindingSeverityBadge finding={finding} />
                {isImplied && <span id={includedId} className={styles.visuallyHidden}>Included with “{parentTitle}”</span>}
                <Text size={200} className={styles.created}>
                  <time id={createdId} dateTime={finding.created_at}>
                    {new Date(finding.created_at).toLocaleString()}
                  </time>
                </Text>
              </div>
              {subFindings.length > 0 && renderRows(subFindings, finding.title)}
            </li>
          )
        })}
      </ul>
    )
    return (
      <>
        <Checkbox label="Select all" checked={selectAllChecked} disabled={exporting}
          onChange={() => { setSelected(allSelected ? NO_SELECTION : new Set(roots.map(finding => finding.id))) }} />
        {renderRows(roots)}
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
            {tooMany && (
              <MessageBar className={styles.messageBar} intent="warning" aria-live="polite"><MessageBarBody>
                Select at most {MAX_EXPORT_FINDINGS.toLocaleString()} findings to download at once.
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
            <Button appearance="primary" className={styles.button} disabled={selectedIds.length === 0 || tooMany}
              disabledFocusable={downloadKeepsFocus} onClick={() => { void download() }}>
              {exporting ? 'Preparing download…' : `Download ${FORMATS[format].name}`}
            </Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  )
}
