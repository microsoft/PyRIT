import { useCallback, useEffect, useState } from 'react'

import {
  Badge,
  Button,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
  Spinner,
  Table,
  TableBody,
  TableCell,
  TableHeader,
  TableHeaderCell,
  TableRow,
  Text,
} from '@fluentui/react-components'
import { AddRegular, ArrowSyncRegular, DeleteRegular } from '@fluentui/react-icons'

import { convertersApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { ConverterIdentifier, ConverterInstance } from '@/types'

import CreateConverterDialog from './CreateConverterDialog'
import { useConverterRegistryStyles } from './Registry.styles'

const IDENTIFIER_FIELDS = new Set([
  'class_name',
  'class_module',
  'hash',
  'pyrit_version',
  'eval_hash',
  'children',
  'attributes',
  'supported_input_types',
  'supported_output_types',
])

function formatParameters(identifier: ConverterIdentifier): string {
  const parameters = Object.entries(identifier)
    .filter(([key, value]) => !IDENTIFIER_FIELDS.has(key) && value != null)
    .map(([key, value]) => `${key}: ${typeof value === 'object' ? JSON.stringify(value) : String(value)}`)
  return parameters.join('\n') || '—'
}

interface DataTypeBadgesProps {
  dataTypes: string[] | null | undefined
}

function DataTypeBadges({ dataTypes }: DataTypeBadgesProps) {
  const styles = useConverterRegistryStyles()
  if (!dataTypes?.length) return <Text>—</Text>
  return (
    <div className={styles.typeList}>
      {dataTypes.map((dataType) => (
        <Badge key={dataType} appearance="tint">{dataType.replace('_path', '')}</Badge>
      ))}
    </div>
  )
}

export default function ConverterRegistry() {
  const styles = useConverterRegistryStyles()
  const [converters, setConverters] = useState<ConverterInstance[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [converterToRemove, setConverterToRemove] = useState<ConverterInstance | null>(null)
  const [removing, setRemoving] = useState(false)

  const loadConverters = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await convertersApi.listConverters()
      setConverters(response.items)
    } catch (err) {
      setError(toApiError(err).detail)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    Promise.resolve().then(() => {
      void loadConverters()
    })
  }, [loadConverters])

  const removeConverter = async () => {
    if (!converterToRemove) return
    setRemoving(true)
    setError(null)
    try {
      await convertersApi.deleteConverter(converterToRemove.converter_id)
      setConverterToRemove(null)
      await loadConverters()
    } catch (err) {
      setError(toApiError(err).detail)
    } finally {
      setRemoving(false)
    }
  }

  return (
    <div className={styles.root} data-testid="converter-registry">
      <div className={styles.header}>
        <div className={styles.headerText}>
          <Text as="h1" size={600} weight="semibold">Converter Registry</Text>
          <Text>Manage configured converter instances.</Text>
        </div>
        <div className={styles.actions}>
          <Button
            className={styles.action}
            appearance="subtle"
            icon={<ArrowSyncRegular />}
            disabled={loading}
            onClick={() => void loadConverters()}
          >
            Refresh
          </Button>
          <Button
            className={styles.action}
            appearance="primary"
            icon={<AddRegular />}
            onClick={() => setCreateOpen(true)}
          >
            New Converter
          </Button>
        </div>
      </div>

      {loading && (
        <div className={styles.state}>
          <Spinner label="Loading converters..." />
        </div>
      )}
      {!loading && error && (
        <div className={`${styles.state} ${styles.error}`}>
          <Text>Error: {error}</Text>
        </div>
      )}
      {!loading && !error && converters.length === 0 && (
        <div className={styles.state}>
          <Text size={500} weight="semibold">No Converters Registered</Text>
          <Text>Add a configured converter to the registry.</Text>
          <Button appearance="primary" icon={<AddRegular />} onClick={() => setCreateOpen(true)}>
            Create First Converter
          </Button>
        </div>
      )}
      {!loading && !error && converters.length > 0 && (
        <div className={styles.tableContainer}>
          <Table className={styles.table} aria-label="Converter instances">
            <TableHeader>
              <TableRow>
                <TableHeaderCell>Registry Name</TableHeaderCell>
                <TableHeaderCell>Type</TableHeaderCell>
                <TableHeaderCell>Inputs</TableHeaderCell>
                <TableHeaderCell>Outputs</TableHeaderCell>
                <TableHeaderCell>Parameters</TableHeaderCell>
                <TableHeaderCell>Actions</TableHeaderCell>
              </TableRow>
            </TableHeader>
            <TableBody>
              {converters.map((converter) => (
                <TableRow key={converter.converter_id}>
                  <TableCell className={styles.nameCell}>{converter.converter_id}</TableCell>
                  <TableCell>
                    <Text>{converter.identifier.class_name}</Text>
                    {converter.is_llm_based && <Badge appearance="tint">LLM</Badge>}
                  </TableCell>
                  <TableCell>
                    <DataTypeBadges dataTypes={converter.identifier.supported_input_types} />
                  </TableCell>
                  <TableCell>
                    <DataTypeBadges dataTypes={converter.identifier.supported_output_types} />
                  </TableCell>
                  <TableCell className={styles.parameters}>
                    {formatParameters(converter.identifier)}
                  </TableCell>
                  <TableCell>
                    <Button
                      className={styles.deleteButton}
                      appearance="subtle"
                      icon={<DeleteRegular />}
                      aria-label={`Remove ${converter.converter_id}`}
                      onClick={() => setConverterToRemove(converter)}
                    >
                      Remove
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <CreateConverterDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={() => {
          setCreateOpen(false)
          void loadConverters()
        }}
      />

      <Dialog
        open={converterToRemove !== null}
        onOpenChange={(_, data) => { if (!data.open && !removing) setConverterToRemove(null) }}
      >
        <DialogSurface>
          <DialogBody>
            <DialogTitle>Remove converter?</DialogTitle>
            <DialogContent>
              {converterToRemove
                ? `Remove "${converterToRemove.converter_id}" from the converter registry?`
                : ''}
            </DialogContent>
            <DialogActions>
              <Button disabled={removing} onClick={() => setConverterToRemove(null)}>Cancel</Button>
              <Button
                appearance="primary"
                disabled={removing}
                onClick={() => void removeConverter()}
              >
                {removing ? 'Removing...' : 'Remove'}
              </Button>
            </DialogActions>
          </DialogBody>
        </DialogSurface>
      </Dialog>
    </div>
  )
}
