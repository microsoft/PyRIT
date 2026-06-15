// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import {
  Button,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
  Menu,
  MenuItem,
  MenuList,
  MenuPopover,
  MenuTrigger,
  MessageBar,
  MessageBarBody,
  Select,
  Spinner,
  Tooltip,
} from '@fluentui/react-components'
import { FlashRegular } from '@fluentui/react-icons'
import type { NodeProps } from '@xyflow/react'
import { useEffect, useMemo, useRef, useState } from 'react'

import type { ConverterNode } from '../../runner/treeTypes'
import { convertersApi } from '../../services/api'
import { toApiError } from '../../services/errors'
import type { ConverterCatalogEntry } from '../../types'
import { useActionCallbacks } from './actionCallbacksContext'
import { useAvailableConverters } from './availableConvertersContext'
import type { TreeFlowNode } from './conversationTreeToReactFlow'
import { CardBody, CardFrame, MetaRow } from './cardFrame'
import ConverterParams from '../Chat/ConverterPanel/ConverterParams'
import { ConverterChipRow } from './UserTurnCard'

type ConverterProps = NodeProps<Extract<TreeFlowNode, { type: 'converter' }>>

export function ConverterCard({ data, selected }: ConverterProps) {
  const node: ConverterNode = data.node
  const callbacks = useActionCallbacks()
  const availableConverters = useAvailableConverters()
  const onSetPipeline = callbacks?.onSetConverterNodePipeline
  const [configureOpen, setConfigureOpen] = useState(false)
  const pipeline = node.params.pipeline
  const label = pipeline.length > 0 ? 'Converter' : 'Choose converter'
  const body = node.params.label ?? (pipeline.length > 0 ? 'Transform prompt before response' : 'Configure a converter for this branch')
  const kindActions = onSetPipeline !== undefined ? (
    <>
      <Menu>
        <MenuTrigger disableButtonEnhancement>
          <Tooltip content="Choose converter" relationship="description">
            <Button size="small" appearance="subtle" icon={<FlashRegular />} aria-label="Choose converter" />
          </Tooltip>
        </MenuTrigger>
        <MenuPopover>
          <MenuList>
            {(availableConverters ?? []).map((converter) => (
              <MenuItem
                key={converter.id}
                onClick={() => onSetPipeline(node.id, [{ converterId: converter.id, inline: { type: converter.label, params: {} } }])}
              >
                {converter.label}
              </MenuItem>
            ))}
            <MenuItem onClick={() => setConfigureOpen(true)}>Configure converter...</MenuItem>
          </MenuList>
        </MenuPopover>
      </Menu>
    </>
  ) : undefined
  return (
    <>
      <CardFrame
        kindLabel={label}
        state={node.state}
        nodeId={node.id}
        selected={selected}
        branchLabel="Branch from here"
        fanChildInfo={data.fanChildInfo}
        kindActions={kindActions}
      >
        <CardBody text={body} />
        {pipeline.length > 0 ? (
          <ConverterChipRow converters={pipeline} available={availableConverters} />
        ) : (
          <MetaRow label="status" value="not configured" />
        )}
        {node.params.preview !== undefined && (
          <MetaRow label="preview" value={node.params.preview.dataType} />
        )}
      </CardFrame>
      {onSetPipeline !== undefined && (
        <ConfigureConverterDialog
          open={configureOpen}
          onOpenChange={setConfigureOpen}
          onConfigure={(converter) => onSetPipeline(node.id, [converter])}
        />
      )}
    </>
  )
}

function ConfigureConverterDialog({
  open,
  onOpenChange,
  onConfigure,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onConfigure: (converter: { converterId: string; inline: { type: string; params: Record<string, unknown> } }) => void
}) {
  const [catalog, setCatalog] = useState<ConverterCatalogEntry[]>([])
  const [selectedType, setSelectedType] = useState('')
  const [paramValues, setParamValues] = useState<Record<string, string>>({})
  const [showValidation, setShowValidation] = useState(false)
  const loadingRef = useRef(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const loading = open && catalog.length === 0 && error === null

  useEffect(() => {
    if (!open || catalog.length > 0 || loadingRef.current) return
    let cancelled = false
    loadingRef.current = true
    convertersApi.listConverterCatalog()
      .then((response) => {
        if (cancelled) return
        setCatalog(response.items)
        setError(null)
      })
      .catch((err) => {
        if (!cancelled) setError(toApiError(err).detail)
      })
      .finally(() => {
        loadingRef.current = false
      })
    return () => {
      cancelled = true
    }
  }, [catalog.length, open])

  const selected = useMemo(
    () => catalog.find((converter) => converter.converter_type === selectedType),
    [catalog, selectedType],
  )
  const missingRequired = selected?.parameters.filter((param) => param.required && !paramValues[param.name]?.trim()) ?? []

  const onSelectType = (type: string) => {
    setSelectedType(type)
    const converter = catalog.find((item) => item.converter_type === type)
    const defaults: Record<string, string> = {}
    for (const param of converter?.parameters ?? []) {
      if (param.default_value !== null && param.default_value !== undefined) defaults[param.name] = param.default_value
    }
    setParamValues(defaults)
    setShowValidation(false)
    setError(null)
  }

  const onSave = async () => {
    if (selected === undefined) return
    if (missingRequired.length > 0) {
      setShowValidation(true)
      return
    }
    setSaving(true)
    setError(null)
    try {
      const response = await convertersApi.createConverter({ type: selected.converter_type, params: paramValues })
      onConfigure({
        converterId: response.converter_id,
        inline: { type: selected.converter_type, params: paramValues },
      })
      onOpenChange(false)
    } catch (err) {
      setError(toApiError(err).detail)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(_event, data) => onOpenChange(data.open)}>
      <DialogSurface>
        <DialogBody>
          <DialogTitle>Configure converter</DialogTitle>
          <DialogContent>
            {loading && <Spinner size="small" label="Loading converters" />}
            {error !== null && (
              <MessageBar intent="error">
                <MessageBarBody>{error}</MessageBarBody>
              </MessageBar>
            )}
            <Select
              aria-label="Converter type"
              value={selectedType}
              onChange={(_event, data) => onSelectType(data.value)}
            >
              <option value="">Choose converter type</option>
              {catalog.map((converter) => (
                <option key={converter.converter_type} value={converter.converter_type}>
                  {converter.converter_type}
                </option>
              ))}
            </Select>
            {selected !== undefined && (
              <ConverterParams
                converter={selected}
                paramValues={paramValues}
                paramsExpanded
                showValidation={showValidation}
                onParamChange={(name, value) => setParamValues((prev) => ({ ...prev, [name]: value }))}
                onFileBrowse={() => undefined}
                onToggleExpanded={() => undefined}
              />
            )}
          </DialogContent>
          <DialogActions>
            <Button appearance="secondary" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button appearance="primary" disabled={selected === undefined || saving} onClick={onSave}>
              {saving ? 'Saving...' : 'Save'}
            </Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  )
}
