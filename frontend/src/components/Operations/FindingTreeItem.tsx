import { useEffect, useState } from 'react'

import { Badge, Button, MessageBar, MessageBarBody, Spinner, Text } from '@fluentui/react-components'
import { ChevronDownRegular, ChevronRightRegular } from '@fluentui/react-icons'

import { operationsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { FindingListItem } from '@/types'

import FindingEvidenceList from './FindingEvidenceList'
import FindingPathsPanel from './FindingPathsPanel'
import FindingSeverityBadge from './FindingSeverityBadge'
import { useFindingTreeItemStyles } from './FindingTreeItem.styles'
import { useOperationsStyles } from './Operations.styles'

const CHILD_PAGE_SIZE = 100
// The operation is the page's h1, so findings start at h2 and sub-findings nest until the deepest heading.
const HEADINGS = ['h2', 'h3', 'h4', 'h5', 'h6'] as const

/** What the page does when an operator acts on a finding in the tree. */
export interface FindingTreeActions {
  onToggle: (findingId: string) => void
  onEdit: (finding: FindingListItem, parentTitle: string | undefined, trigger: HTMLElement) => void
  onAddSubFinding: (parent: FindingListItem, trigger: HTMLElement) => void
  onDelete: (finding: FindingListItem, trigger: HTMLElement) => void
  onEditPaths: (parent: FindingListItem, subFindings: FindingListItem[], trigger: HTMLElement) => void
  onEvidenceDetached: () => void
}

interface FindingTreeItemProps {
  operationId: string
  finding: FindingListItem
  depth: number
  /** The parent's title, when this is a sub-finding. */
  parentTitle?: string
  /** Changes whenever findings change, so expanded sub-findings reload. */
  revision: number
  expanded: ReadonlySet<string>
  actions: FindingTreeActions
}

type ChildrenLoad =
  | { key: string; status: 'loaded'; children: FindingListItem[] }
  | { key: string; status: 'error'; error: string }

/** One finding, its evidence and actions, and its sub-findings and paths when expanded. */
export default function FindingTreeItem({
  operationId, finding, depth, parentTitle, revision, expanded, actions,
}: FindingTreeItemProps) {
  const styles = useOperationsStyles()
  const treeStyles = useFindingTreeItemStyles()
  const Heading = HEADINGS[Math.min(depth, HEADINGS.length - 1)] ?? 'h6'
  const isExpanded = expanded.has(finding.id)
  const childCount = finding.child_count
  return (
    <li className={styles.item}>
      <Heading className={styles.title}>{finding.title}</Heading>
      <div className={styles.metadata}>
        <FindingSeverityBadge finding={finding} />
        {!finding.demonstrated && (
          <Badge className={treeStyles.badge} appearance="outline" color="warning">Not demonstrated</Badge>
        )}
        <time dateTime={finding.created_at}>{new Date(finding.created_at).toLocaleString()}</time>
      </div>
      {finding.harm_type && <Text>Harm-type: {finding.harm_type === 'Other' ? finding.harm_type_other : finding.harm_type}</Text>}
      {finding.description && <p className={styles.description}>{finding.description}</p>}
      <FindingEvidenceList operationId={operationId} findingId={finding.id} count={finding.evidence_count}
        onDetached={actions.onEvidenceDetached} actions={<>
          <Button className={styles.button} aria-label={`Edit finding: ${finding.title}`}
            onClick={event => { actions.onEdit(finding, parentTitle, event.currentTarget) }}>Edit</Button>
          <Button className={styles.button} aria-label={`Add sub-finding: ${finding.title}`}
            onClick={event => { actions.onAddSubFinding(finding, event.currentTarget) }}>Add sub-finding</Button>
          <Button className={styles.button} aria-label={`Delete finding: ${finding.title}`}
            onClick={event => { actions.onDelete(finding, event.currentTarget) }}>Delete</Button>
        </>} />
      {childCount > 0 && (
        <Button className={treeStyles.toggle} aria-expanded={isExpanded}
          aria-label={`Sub-findings (${childCount}): ${finding.title}`}
          icon={isExpanded ? <ChevronDownRegular /> : <ChevronRightRegular />}
          onClick={() => { actions.onToggle(finding.id) }}>Sub-findings ({childCount})</Button>
      )}
      {childCount > 0 && isExpanded && (
        <FindingChildren operationId={operationId} parent={finding} depth={depth + 1} revision={revision}
          expanded={expanded} actions={actions} />
      )}
    </li>
  )
}

interface FindingChildrenProps {
  operationId: string
  parent: FindingListItem
  depth: number
  revision: number
  expanded: ReadonlySet<string>
  actions: FindingTreeActions
}

/** A finding's paths and every one of its direct sub-findings, which the paths choose their steps from. */
function FindingChildren({ operationId, parent, depth, revision, expanded, actions }: FindingChildrenProps) {
  const treeStyles = useFindingTreeItemStyles()
  const [retry, setRetry] = useState(0)
  const [load, setLoad] = useState<ChildrenLoad | null>(null)
  const requestKey = JSON.stringify([operationId, parent.id, revision, retry])

  useEffect(() => {
    let ignore = false
    const loadAllPages = async (): Promise<FindingListItem[] | null> => {
      const loaded = new Map<string, FindingListItem>()
      const seenOffsets = new Set<number>()
      let offset: number | null = 0
      while (offset !== null) {
        // A repeated offset means the server is not advancing the page; fetching it again would loop forever.
        if (seenOffsets.has(offset)) throw new Error('The sub-findings page offset did not advance.')
        seenOffsets.add(offset)
        const page = await operationsApi.listFindings(operationId, {
          limit: CHILD_PAGE_SIZE, offset, parent_finding_id: parent.id,
        })
        if (ignore) return null
        // A sub-finding added while pages load shifts later pages, so a page can repeat an earlier one.
        for (const child of page.items) {
          if (!loaded.has(child.id)) loaded.set(child.id, child)
        }
        offset = page.has_more ? page.next_offset : null
      }
      return [...loaded.values()]
    }
    loadAllPages()
      .then(children => { if (children !== null) setLoad({ key: requestKey, status: 'loaded', children }) })
      .catch((cause: unknown) => {
        if (!ignore) setLoad({ key: requestKey, status: 'error', error: toApiError(cause).detail })
      })
    return () => { ignore = true }
  }, [operationId, parent.id, requestKey])

  const current = load?.key === requestKey ? load : null
  return (
    <div role="region" aria-label={`Sub-findings of ${parent.title}`} className={treeStyles.children}>
      {current === null ? <Spinner size="small" label="Loading sub-findings…" /> : current.status === 'error' ? (
        <MessageBar intent="error"><MessageBarBody>
          Could not load sub-findings: {current.error}{' '}
          <Button className={treeStyles.button} onClick={() => { setRetry(value => value + 1) }}>Retry sub-findings</Button>
        </MessageBarBody></MessageBar>
      ) : (
        <>
          <FindingPathsPanel parent={parent} subFindings={current.children}
            onEdit={trigger => { actions.onEditPaths(parent, current.children, trigger) }} />
          <ul className={treeStyles.childList}>
            {current.children.map(child => (
              <FindingTreeItem key={child.id} operationId={operationId} finding={child} depth={depth}
                parentTitle={parent.title} revision={revision} expanded={expanded} actions={actions} />
            ))}
          </ul>
        </>
      )}
    </div>
  )
}
