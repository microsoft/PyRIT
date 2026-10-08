import { useCallback, useEffect, useRef, useState } from 'react'

import {
  Badge, Button, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface,
  DialogTitle, DialogTrigger, Field, Input, MessageBar, MessageBarBody, Select, Spinner,
  Table, TableBody, TableCell, TableHeader, TableHeaderCell, TableRow, Text,
} from '@fluentui/react-components'
import { AddRegular, ArrowSyncRegular } from '@fluentui/react-icons'

import { useRuntime } from '@/hooks/useRuntime'
import { techniquesApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { TechniqueInstance } from '@/types'

import CreateTechniqueDialog from './CreateTechniqueDialog'
import { useTechniqueRegistryStyles } from './TechniqueRegistry.styles'

interface TechniqueRegistryPageProps {
  ready: boolean
  runtimeKey: string
}

function TechniqueRegistryPage({ ready, runtimeKey }: TechniqueRegistryPageProps) {
  const styles = useTechniqueRegistryStyles()
  const [items, setItems] = useState<TechniqueInstance[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [typeFilter, setTypeFilter] = useState('')
  const [tagFilter, setTagFilter] = useState('')
  const [createOpen, setCreateOpen] = useState(false)
  const [detail, setDetail] = useState<TechniqueInstance | null>(null)
  const [focusRestore, setFocusRestore] = useState(0)
  const mounted = useRef(true)
  const listEpoch = useRef(0)
  const newButton = useRef<HTMLButtonElement>(null)
  const pageRoot = useRef<HTMLDivElement>(null)
  const previousRuntime = useRef(runtimeKey)

  const load = useCallback(async (): Promise<void> => {
    const epoch = ++listEpoch.current
    setLoading(true)
    setError(null)
    try {
      const response = await techniquesApi.listTechniques()
      if (mounted.current && epoch === listEpoch.current) setItems(response.items)
    } catch (err) {
      if (mounted.current && epoch === listEpoch.current) setError(toApiError(err).detail)
    } finally {
      if (mounted.current && epoch === listEpoch.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    mounted.current = true
    listEpoch.current++
    if (previousRuntime.current !== runtimeKey) {
      previousRuntime.current = runtimeKey
      setItems([])
      setSearch('')
      setTypeFilter('')
      setTagFilter('')
      setCreateOpen(false)
      setDetail(null)
      setFocusRestore((current) => current + 1)
    }
    void Promise.resolve().then(() => { if (mounted.current) void load() })
    return () => {
      mounted.current = false
    }
  }, [load, runtimeKey])

  useEffect(() => {
    if (focusRestore) {
      if (newButton.current?.disabled) pageRoot.current?.focus()
      else newButton.current?.focus()
    }
  }, [focusRestore])

  const closeCreate = (): void => {
    setCreateOpen(false)
    setFocusRestore((current) => current + 1)
  }

  const visible = items.filter((item) => (
    `${item.name} ${item.description ?? ''} ${item.attack_type} ${item.tags.join(' ')}`.toLowerCase().includes(search.toLowerCase())
    && (!typeFilter || item.attack_type === typeFilter)
    && (!tagFilter || item.tags.includes(tagFilter))
  ))
  const types = [...new Set(items.map((item) => item.attack_type))].sort()
  const tags = [...new Set(items.flatMap((item) => item.tags))].sort()

  return (
    <div className={styles.root} ref={pageRoot} tabIndex={-1}>
      <div className={styles.header}>
        <div className={styles.headerText}>
          <Text as="h1" size={600} weight="semibold">Technique Registry</Text>
          <Text size={300} className={styles.subtitle}>Named configurations of existing attack techniques</Text>
        </div>
        <div className={styles.headerActions}>
          <Button className={styles.headerAction} appearance="subtle" icon={<ArrowSyncRegular />}
            disabled={loading || !ready} onClick={() => { void load() }}>Refresh</Button>
          <Button className={styles.headerAction} ref={newButton} appearance="primary" icon={<AddRegular />} disabled={!ready}
            onClick={() => setCreateOpen(true)}>New technique</Button>
        </div>
      </div>
      <div className={styles.filters}>
        <Field label="Search techniques"><Input value={search} onChange={(_, data) => setSearch(data.value)} /></Field>
        <Field label="Filter by attack type"><Select value={typeFilter} onChange={(_, data) => setTypeFilter(data.value)}>
          <option value="">All attack types</option>
          {types.map((type) => <option key={type} value={type}>{type}</option>)}
        </Select></Field>
        <Field label="Filter by tag"><Select value={tagFilter} onChange={(_, data) => setTagFilter(data.value)}>
          <option value="">All tags</option>
          {tags.map((tag) => <option key={tag} value={tag}>{tag}</option>)}
        </Select></Field>
      </div>
      {loading && <Spinner label="Loading techniques..." />}
      {!loading && error && <MessageBar intent="error"><MessageBarBody>{error} <Button onClick={() => { void load() }}>Retry</Button></MessageBarBody></MessageBar>}
      {!loading && !error && items.length === 0 && <Text>No techniques registered. Add a technique or run a technique initializer.</Text>}
      {!loading && !error && items.length > 0 && visible.length === 0 && <Text>No techniques match these filters.</Text>}
      {!loading && !error && visible.length > 0 && (
        <div className={styles.table}>
          <Table className={styles.tableContent} aria-label="Registered techniques">
            <TableHeader className={styles.tableHeader}><TableRow>
              <TableHeaderCell>Name</TableHeaderCell><TableHeaderCell>Description</TableHeaderCell>
              <TableHeaderCell>Attack type</TableHeaderCell><TableHeaderCell>Tags</TableHeaderCell><TableHeaderCell>Details</TableHeaderCell>
            </TableRow></TableHeader>
            <TableBody>{visible.map((item) => (
              <TableRow key={item.name}>
                <TableCell className={styles.nameCell}>{item.name}</TableCell><TableCell>{item.description ?? 'No description'}</TableCell>
                <TableCell className={styles.nameCell}>{item.attack_type}</TableCell>
                <TableCell><div className={styles.tags}>{item.tags.map((tag) => <Badge key={tag}>{tag}</Badge>)}</div></TableCell>
                <TableCell>
                  <Dialog open={detail?.name === item.name} onOpenChange={(_, data) => {
                    if (!data.open) setDetail(null)
                  }}>
                    <DialogTrigger disableButtonEnhancement>
                      <Button className={styles.action} onClick={() => setDetail(item)} aria-label={`Details for ${item.name}`}>Details</Button>
                    </DialogTrigger>
                    <DialogSurface>
                      <DialogBody>
                        <DialogTitle>{item.name}</DialogTitle>
                        <DialogContent className={styles.content}>
                          {detail && <pre className={styles.creationCall} aria-label="Technique creation call">{detail.creation_statement}</pre>}
                        </DialogContent>
                        <DialogActions><DialogTrigger disableButtonEnhancement><Button>Close</Button></DialogTrigger></DialogActions>
                      </DialogBody>
                    </DialogSurface>
                  </Dialog>
                </TableCell>
              </TableRow>
            ))}</TableBody>
          </Table>
        </div>
      )}
      {createOpen && <CreateTechniqueDialog onClose={closeCreate} onCreated={() => { closeCreate(); void load() }} />}
    </div>
  )
}

export default function TechniqueRegistry() {
  const { generation, ready } = useRuntime()
  return <TechniqueRegistryPage runtimeKey={`${generation}-${ready}`} ready={ready} />
}
