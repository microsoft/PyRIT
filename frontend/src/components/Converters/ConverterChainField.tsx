import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'

import { MessageBar, MessageBarBody, Text } from '@fluentui/react-components'

import CreateConverterDialog from '@/components/Registry/CreateConverterDialog'
import { convertersApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { ConverterIdentifier, ConverterInstance, ConverterSelection, SourceInstanceSpec } from '@/types'
import { groupConverters } from '@/utils/groupConverters'

import { useConverterChainFieldStyles } from './ConverterChainField.styles'
import SelectConverterInput from './SelectConverterInput'
import ConverterCard from './ConverterCard'

interface ConverterChainFieldProps {
  label: string
  converters: ConverterInstance[]
  value: ConverterSelection[]
  disabled?: boolean
  allowTemporary?: boolean
  onChange: (value: ConverterSelection[]) => void
  onConvertersChanged: (converters: ConverterInstance[]) => void
}

/** Ordered converter references with the same picker and settings form as chat. */
export default function ConverterChainField({
  label, converters, value, disabled = false, allowTemporary = true, onChange, onConvertersChanged,
}: ConverterChainFieldProps) {
  const styles = useConverterChainFieldStyles()
  const id = useId()
  const [entryIds, setEntryIds] = useState<string[]>([])
  const nextId = useRef(0)
  const dragged = useRef<number | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [settings, setSettings] = useState<{
    index: number; editing: { converter: ConverterInstance; spec?: SourceInstanceSpec }
  }>()
  const [error, setError] = useState<string>()
  const active = useRef(true)
  useEffect(() => {
    active.current = true
    return () => { active.current = false }
  }, [])
  const ids = value.map((_, index) => entryIds[index] ?? `${id}-initial-${index}`)
  const latest = useRef({ value, ids, onChange })
  useLayoutEffect(() => { latest.current = { value, ids, onChange } })

  const add = (name: string): void => {
    const current = latest.current
    const next = {
      value: [...current.value, name],
      ids: [...current.ids, `${id}-added-${nextId.current++}`],
      onChange: current.onChange,
    }
    latest.current = next
    setEntryIds(next.ids)
    next.onChange(next.value)
  }
  const move = (index: number, delta: number): void => {
    if (index + delta < 0 || index + delta >= value.length) return
    const next = [...value]
    const [entry] = next.splice(index, 1)
    next.splice(index + delta, 0, entry)
    const [entryId] = ids.splice(index, 1)
    ids.splice(index + delta, 0, entryId)
    setEntryIds(ids)
    onChange(next)
  }

  return <section className={styles.root} aria-label={label}>
    <Text weight="semibold">{label}</Text>
    <SelectConverterInput
      label={label} disabled={disabled} groupedConverters={groupConverters(converters)}
      onOptionSelect={add} onCreateNew={() => setCreateOpen(true)}
    />
    {error && <MessageBar intent="error"><MessageBarBody>{error}</MessageBarBody></MessageBar>}
    {value.map((selection, index) => {
      const name = typeof selection === 'string' ? selection : selection.source_name
      const converter = converters.find((entry) => entry.converter_id === name)
      return <ConverterCard key={ids[index]} name={name} converter={converter} disabled={disabled}
        context={`, ${label} stage ${index + 1}`} temporary={typeof selection !== 'string'}
        onSettings={allowTemporary ? () => {
            if (converter) setSettings({
              index, editing: { converter, spec: typeof selection === 'string' ? undefined : selection },
            })
          } : undefined}
        onReset={() => onChange(value.map((entry, position) => position === index ? name : entry))}
        onMove={(delta) => move(index, delta)}
        onDragStart={(event) => {
          dragged.current = index
          event.dataTransfer.effectAllowed = 'move'
          event.dataTransfer.setData('text/plain', String(index))
        }}
        onDragEnd={() => { dragged.current = null }}
        onDragOver={(event) => { if (dragged.current !== null) event.preventDefault() }}
        onDrop={(event) => {
          event.preventDefault()
          if (dragged.current !== null) move(dragged.current, index - dragged.current)
          dragged.current = null
        }}
        onRemove={() => {
            setEntryIds(ids.filter((_, position) => position !== index))
            onChange(value.filter((_, position) => position !== index))
          }}
      />
    })}
    {createOpen && <CreateConverterDialog open onClose={() => setCreateOpen(false)}
      onCreated={(name: string, isCurrentOpening: boolean) => {
        if (!active.current) return
        if (isCurrentOpening) setCreateOpen(false)
        add(name)
        void convertersApi.listConverters().then((response) => {
          if (!active.current) return
          onConvertersChanged(response.items)
          setError(undefined)
        }).catch((err: unknown) => {
          if (active.current) setError(toApiError(err).detail)
        })
      }}
    />}
    {settings && <CreateConverterDialog open editing={settings.editing} allowUploads={false}
      onClose={() => setSettings(undefined)}
      onCreated={() => { throw new Error('Temporary settings must not register a converter.') }}
      onTemporary={(spec: SourceInstanceSpec, identifier: ConverterIdentifier) => {
        onChange(value.map((entry, index) => index === settings.index
          ? { ...spec, effective_hash: identifier.hash } : entry))
        setSettings(undefined)
      }}
    />}
  </section>
}
