import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import {
  Text,
  Button,
  Input,
  Badge,
  Tooltip,
  Popover,
  PopoverTrigger,
  PopoverSurface,
} from '@fluentui/react-components'
import {
  DismissRegular,
  WarningRegular,
  TagRegular,
} from '@fluentui/react-icons'
import { labelsApi } from '@/services/api'
import OperationPicker from './OperationPicker'
import { useLabelsBarStyles } from './LabelsBar.styles'


const validateValue = (value: string): string | null => {
  if (!value) return 'Value is required'
  if (value.length > 128) return 'Values must be 128 characters or fewer'
  if (value !== value.toLowerCase()) return 'Values must be lowercase'
  if (!/^[a-z0-9_]+$/.test(value)) return 'Only lowercase letters, numbers, underscores'
  return null
}

const DUMMY_VALUES: Record<string, string> = {
  operator: 'roakey',
  operation: 'op_trash_panda',
}

const METADATA_KEYS = new Set(['operator', 'operation'])

interface LabelsBarProps {
  labels: Record<string, string>
  onLabelsChange: (labels: Record<string, string>) => void
  operatorReadOnly?: boolean
}

export default function LabelsBar({ labels, onLabelsChange, operatorReadOnly = false }: LabelsBarProps) {
  const styles = useLabelsBarStyles()
  const [isPopoverOpen, setIsPopoverOpen] = useState(false)
  const [newKey, setNewKey] = useState('')
  const [newValue, setNewValue] = useState('')
  const [editingLabel, setEditingLabel] = useState<string | null>(null)
  const [editValue, setEditValue] = useState('')
  const [error, setError] = useState('')
  const [existingLabels, setExistingLabels] = useState<Record<string, string[]>>({})
  const editInputRef = useRef<HTMLInputElement>(null)
  // Both editors finish their work on blur, one turn later, so that focus lands
  // first. By then the click that took the focus may already have started a
  // different edit, which this has to be able to notice. Counting the edits is
  // what tells them apart: the same label can be picked up again in between.
  const editSession = useRef(0)
  // A save waits out the blur so focus can land first. If the edit it belongs
  // to finishes another way in the meantime — a suggestion picked, the label
  // removed — that save has to be called off, or it lands afterwards with the
  // value it was typed with. Starting a different edit is not the same thing:
  // that save is still the user's and still has to land.
  const pendingSaves = useRef(new Map<number, ReturnType<typeof setTimeout>>())

  const cancelPendingSave = (session: number) => {
    const timer = pendingSaves.current.get(session)
    if (timer === undefined) return
    clearTimeout(timer)
    pendingSaves.current.delete(session)
  }
  // That late save also has to write onto the labels as they are by then, not
  // the ones it was looking at when the blur happened.
  const labelsRef = useRef(labels)
  useEffect(() => { labelsRef.current = labels }, [labels])

  // Writing through the ref as well means a save still waiting its turn works
  // off the labels as this component last left them, rather than depending on
  // the parent having re-rendered in the meantime.
  const commitLabels = (next: Record<string, string>) => {
    labelsRef.current = next
    onLabelsChange(next)
  }

  // Fetch existing label keys/values for suggestions
  useEffect(() => {
    labelsApi.getLabels()
      // A name created while this was in flight is not in the response yet,
      // so keep anything already collected rather than replacing outright.
      .then(resp => setExistingLabels(prev => ({
        ...resp.labels,
        // TODO(PyRIT 1.4): Remove the labels.* fallbacks with legacy attribution aliases.
        operator: [...new Set([...(resp.operators ?? resp.labels.operator ?? []), ...(prev.operator || [])])],
        operation: [...new Set([...(resp.operations ?? resp.labels.operation ?? []), ...(prev.operation || [])])],
      })))
      .catch(() => setError('Could not load label suggestions.'))
  }, [])

  const isDummyValue = useCallback((key: string, value: string): boolean => {
    return DUMMY_VALUES[key] === value
  }, [])

  const placeholderKeys = Object.entries(labels)
    .filter(([key, value]) => isDummyValue(key, value))
    .map(([key]) => key)
  const hasDummyValues = placeholderKeys.length > 0

  const validateKey = (key: string): string | null => {
    if (!key) return 'Key is required'
    if (key !== key.toLowerCase()) return 'Labels must be lowercase'
    if (!/^[a-z][a-z0-9_]*$/.test(key)) return 'Only lowercase letters, numbers, underscores'
    if (key in labels) return 'Label key already exists'
    if (METADATA_KEYS.has(key)) return 'Set operator and operation in the bar'
    return null
  }

  const handleAddLabel = () => {
    const keyError = validateKey(newKey)
    if (keyError) { setError(keyError); return }
    const valueError = validateValue(newValue)
    if (valueError) { setError(valueError); return }

    commitLabels({ ...labelsRef.current, [newKey]: newValue })
    setNewKey('')
    setNewValue('')
    setError('')
    setIsPopoverOpen(false)
  }

  const handleRemoveLabel = (key: string) => {
    if (key === 'operator') return
    // The label may be open for editing in the popover while its chip is still
    // on the bar, and that edit has a save on the way. Taking the label away
    // has to take the save with it, or it comes back a moment later.
    if (editingLabel === key) {
      cancelPendingSave(editSession.current)
      endEdit(editSession.current)
    }
    const next = { ...labelsRef.current }
    delete next[key]
    commitLabels(next)
  }

  const handleStartEdit = (key: string) => {
    if (key === 'operator' && operatorReadOnly) return
    editSession.current += 1
    setEditingLabel(key)
    setEditValue(labels[key] ?? '')
    setError('')
    if (key !== 'operator') setTimeout(() => editInputRef.current?.focus(), 50)
  }

  const handleStartEditKeyDown = (e: React.KeyboardEvent, key: string) => {
    // Let focusable children (the remove button) handle their own keys.
    if (e.target !== e.currentTarget) return
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      handleStartEdit(key)
    }
  }

  /** Ends an edit, unless the user has already started a different one. */
  const endEdit = (session: number) => {
    if (editSession.current !== session) return
    setEditingLabel(null)
    setEditValue('')
    setError('')
  }

  const saveEdit = (key: string, session: number) => {
    const valueError = validateValue(editValue)
    // Only the edit still on screen may speak for itself; a value left behind
    // is simply not saved rather than complaining next to somebody else.
    if (valueError) {
      if (editSession.current === session) setError(valueError)
      return
    }
    commitLabels({ ...labelsRef.current, [key]: editValue })
    endEdit(session)
  }

  const handleEditKeyDown = (e: React.KeyboardEvent, key: string, session: number) => {
    if (e.key === 'Enter') saveEdit(key, session)
    if (e.key === 'Escape') endEdit(session)
  }

  const handleSelectOperation = (operation: string) => {
    commitLabels({ ...labelsRef.current, operation })
    setEditingLabel(null)
    setEditValue('')
    setError('')
  }

  const handleAddKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') handleAddLabel()
    if (e.key === 'Escape') setIsPopoverOpen(false)
  }

  // Suggestions: show existing keys not yet used, and values for the current key
  const suggestedKeys = Object.keys(existingLabels)
    .filter(key => !METADATA_KEYS.has(key) && !(key in labels))
  const suggestedValues = (editingLabel ? existingLabels[editingLabel] : existingLabels[newKey]) || []

  // Layout: the labels icon (with custom-label count badge) is always the first
  // element on the bar. We then render as many full chips as fit, in
  // declaration order. Metadata stays in the bar; custom labels that do not
  // fit remain available in the popover.
  const rootRef = useRef<HTMLDivElement>(null)
  const measureRef = useRef<HTMLDivElement>(null)
  const metadataRef = useRef<HTMLDivElement>(null)
  const ICON_BUTTON_WIDTH_PX = 56  // labels icon + count badge + gap
  const ADD_BUTTON_WIDTH_PX = 60   // "+ Add" button
  const [visibleCount, setVisibleCount] = useState(Infinity)

  const headerEntries = useMemo(() => Object.entries(labels).filter(([key]) => !METADATA_KEYS.has(key)), [labels])
  const labelEntries = useMemo(
    () => headerEntries.filter(([key]) => !METADATA_KEYS.has(key)),
    [headerEntries]
  )

  useEffect(() => {
    const root = rootRef.current
    const measure = measureRef.current
    if (!root || !measure) return

    const check = () => {
      const rootW = root.clientWidth
      // jsdom and pre-layout: keep all chips so unit tests remain stable.
      if (rootW === 0) { setVisibleCount(Infinity); return }

      const chips = Array.from(measure.querySelectorAll('[data-label-idx]')) as HTMLElement[]
      if (chips.length === 0) { setVisibleCount(Infinity); return }

      // Sum chip widths in order until we exceed available space. We
      // measure against the off-screen `measure` row that has the same
      // styling as the inline row but is allowed to lay out at full
      // width, so each chip's offsetWidth reflects its natural size.
      const gap = 4
      const reserved = ICON_BUTTON_WIDTH_PX + gap + (metadataRef.current?.offsetWidth ?? 0)
      const available = rootW - reserved
      let used = 0
      let count = 0
      for (const chip of chips) {
        const next = used + chip.offsetWidth + (count > 0 ? gap : 0)
        if (next > available) break
        used = next
        count++
      }
      // If everything fits, also reserve room for the inline "+ Add"
      // button. Drop the last chip(s) until "+ Add" fits too.
      if (count === chips.length) {
        const withAdd = used + gap + ADD_BUTTON_WIDTH_PX
        if (withAdd > available) {
          // Recompute with the +Add allowance baked in.
          used = 0
          count = 0
          const availableWithAdd = available - ADD_BUTTON_WIDTH_PX - gap
          for (const chip of chips) {
            const next = used + chip.offsetWidth + (count > 0 ? gap : 0)
            if (next > availableWithAdd) break
            used = next
            count++
          }
        }
      }
      setVisibleCount(count)
    }

    const observer = new ResizeObserver(check)
    observer.observe(root)
    if (metadataRef.current) observer.observe(metadataRef.current)
    if (root.parentElement) observer.observe(root.parentElement)
    check()
    return () => observer.disconnect()
  }, [headerEntries])

  const renderValueEditor = (key: string, value: string) => {
    // Whatever is deferred below belongs to this edit, and only this one.
    const session = editSession.current
    const filteredSuggestions = suggestedValues
      .filter(v => v !== value && v.includes(editValue))
      .slice(0, 8)
    return (
      <>
        <Text size={200} weight="semibold">{key}:</Text>
        <Input
          ref={editInputRef}
          size="small"
          value={editValue}
          onChange={(_, d) => { setEditValue(d.value.toLowerCase()); setError('') }}
          onKeyDown={e => handleEditKeyDown(e, key, session)}
          onBlur={() => {
            cancelPendingSave(session)
            pendingSaves.current.set(session, setTimeout(() => {
              pendingSaves.current.delete(session)
              saveEdit(key, session)
            }, 150))
          }}
          style={{ width: '120px' }}
          aria-label={`Value for ${key} label`}
          data-testid={`edit-label-${key}`}
        />
        {error && <Text size={200} className={styles.errorText}>{error}</Text>}
        {filteredSuggestions.length > 0 && (
          <div className={styles.editDropdown}>
            {filteredSuggestions.map(v => (
              <Badge
                key={v}
                appearance="outline"
                size="small"
                className={styles.suggestionChip}
                onMouseDown={e => e.preventDefault()}
                onClick={() => {
                  cancelPendingSave(session)
                  commitLabels({ ...labelsRef.current, [key]: v })
                  setEditingLabel(null)
                  setEditValue('')
                }}
              >{v}</Badge>
            ))}
          </div>
        )}
      </>
    )
  }

  const renderLabelBadge = (key: string, value: string, idx: number) => {
    const isDummy = isDummyValue(key, value)
    const isReadOnly = key === 'operator' && operatorReadOnly
    const isRequired = key === 'operator'
    // The popover renders its own editor, so only one is mounted at a time.
    const isEditing = editingLabel === key && !isPopoverOpen

    if (isEditing) {
      // The picker is wider than a plain input, so let its row give way rather
      // than push the control past the edge the bar clips at.
      const canShrink = key === 'operation'
      return (
        <div
          key={key}
          data-label-idx={idx}
          className={styles.inputRow}
          style={{
            display: 'inline-flex',
            position: 'relative',
            flexShrink: canShrink ? 1 : 0,
            minWidth: canShrink ? 0 : undefined,
          }}
        >
          {renderValueEditor(key, value)}
        </div>
      )
    }

    return (
      <div
        key={key}
        data-label-idx={idx}
        className={`${styles.labelBadge} ${isDummy ? styles.labelDummy : styles.labelNormal}`}
        style={{ flexShrink: 0 }}
        // The pill's own padding sits outside the edit control, so a click that
        // lands on it reaches nothing. Forward only those: anything on a child
        // is that child's to handle.
        onClick={e => { if (e.target === e.currentTarget) handleStartEdit(key) }}
      >
        <Tooltip
          content={isReadOnly ? 'Derived from your signed-in account' : isDummy ? `Placeholder value — click to change` : `Click to edit`}
          relationship="description"
        >
          <div
            className={styles.labelEdit}
            onClick={() => handleStartEdit(key)}
            onKeyDown={e => handleStartEditKeyDown(e, key)}
            role="button"
            tabIndex={isReadOnly ? -1 : 0}
            aria-disabled={isReadOnly}
            aria-label={isReadOnly ? `Signed-in operator: ${value}` : key === 'operation' && !value
              ? 'Select operation' : `Edit ${key}${key === 'operation' || isRequired ? '' : ' label'}, currently ${value}`}
            data-testid={`label-${key}`}
          >
            <Text size={200} weight="semibold">{key === 'operation' && !value ? 'Select operation' : `${key}:`}</Text>
            {value && <Text size={200} style={{ whiteSpace: 'nowrap' }}>{value}</Text>}
          </div>
        </Tooltip>
        {!isRequired && value && (
          <Button
            className={styles.removeBtn}
            appearance="transparent"
            size="small"
            icon={<DismissRegular fontSize={12} />}
            onClick={(e) => { e.stopPropagation(); handleRemoveLabel(key) }}
            aria-label={`Remove ${key} label`}
            data-testid={`remove-label-${key}`}
          />
        )}
      </div>
    )
  }

  const renderPopoverEntry = (key: string, value: string) => {
    if (editingLabel === key) {
      return (
        <div key={key} className={styles.inputRow} style={{ position: 'relative' }}>
          {renderValueEditor(key, value)}
        </div>
      )
    }
    return (
      <div
        key={key}
        className={`${styles.labelBadge} ${isDummyValue(key, value) ? styles.labelDummy : styles.labelNormal}`}
        style={{ flexShrink: 0 }}
        onClick={e => { if (e.target === e.currentTarget) handleStartEdit(key) }}
      >
        <div
          className={styles.labelEdit}
          onClick={() => handleStartEdit(key)}
          onKeyDown={e => handleStartEditKeyDown(e, key)}
          role="button"
          tabIndex={0}
          aria-label={`Edit ${key} label, currently ${value}`}
          data-testid={`popover-label-${key}`}
        >
          <Text size={200} weight="semibold">{key}:</Text>
          <Text size={200}>{value}</Text>
        </div>
        <Button
          className={styles.removeBtn}
          appearance="transparent"
          size="small"
          icon={<DismissRegular fontSize={12} />}
          onClick={(e) => { e.stopPropagation(); handleRemoveLabel(key) }}
          aria-label={`Remove ${key} label`}
          data-testid={`popover-remove-label-${key}`}
        />
      </div>
    )
  }

  const renderLabelsList = () => (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
      {labelEntries.map(([key, value]) => renderPopoverEntry(key, value))}
    </div>
  )

  const renderAddForm = () => (
    <>
      <div className={styles.inputRow}>
        <Input
          className={styles.inputField}
          size="small"
          placeholder="key"
          aria-label="Label key"
          value={newKey}
          onChange={(_, d) => { setNewKey(d.value.toLowerCase()); setError('') }}
          onKeyDown={handleAddKeyDown}
          data-testid="new-label-key"
        />
        <Input
          className={styles.inputField}
          size="small"
          placeholder="value"
          aria-label="Label value"
          value={newValue}
          onChange={(_, d) => { setNewValue(d.value.toLowerCase()); setError('') }}
          onKeyDown={handleAddKeyDown}
          data-testid="new-label-value"
        />
        <Button
          appearance="primary"
          size="small"
          onClick={handleAddLabel}
          data-testid="confirm-add-label"
        >
          Add
        </Button>
      </div>
      {suggestedKeys.length > 0 && !newKey && (
        <>
          <Text size={200} weight="semibold">Existing keys:</Text>
          <div className={styles.suggestions}>
            {suggestedKeys.slice(0, 8).map(k => (
              <Badge
                key={k}
                appearance="outline"
                size="small"
                className={styles.suggestionChip}
                onClick={() => setNewKey(k)}
              >{k}</Badge>
            ))}
          </div>
        </>
      )}
      {newKey && suggestedValues.length > 0 && (
        <>
          <Text size={200} weight="semibold">Existing values for "{newKey}":</Text>
          <div className={styles.suggestions}>
            {suggestedValues.slice(0, 8).map(v => (
              <Badge
                key={v}
                appearance="outline"
                size="small"
                className={styles.suggestionChip}
                onClick={() => setNewValue(v)}
              >{v}</Badge>
            ))}
          </div>
        </>
      )}
      {error && !editingLabel && <Text size={200} className={styles.errorText}>{error}</Text>}
    </>
  )

  return (
    <div className={styles.root} data-testid="labels-bar" ref={rootRef}>
      {hasDummyValues && (
        <Popover>
          <PopoverTrigger disableButtonEnhancement>
            <Button
              appearance="subtle"
              size="small"
              icon={<WarningRegular />}
              className={styles.warningIcon}
              data-testid="labels-warning"
              aria-label="Show warnings"
            />
          </PopoverTrigger>
          <PopoverSurface className={styles.popover} aria-label="Warnings">
            <div className={styles.popoverSurface}>
              <Text as="h2" weight="semibold" size={300}>Warnings</Text>
              <Text size={200}>
                {`Set ${placeholderKeys.join(' and ')} in the bar. ${
                  placeholderKeys.length === 1
                    ? 'The current value is a placeholder.'
                    : 'The current values are placeholders.'
                }`}
              </Text>
            </div>
          </PopoverSurface>
        </Popover>
      )}

      {/*
        Off-screen measurement row: contains every chip at its natural
        width so we can compute how many fit. Hidden via CSS but laid out
        normally; ResizeObserver triggers a re-measure on width changes.
      */}
      <div
        ref={measureRef}
        aria-hidden="true"
        className={styles.measureRow}
      >
        {headerEntries.map(([key, value], idx) => (
          <span
            key={key}
            data-label-idx={idx}
            className={`${styles.labelBadge} ${isDummyValue(key, value) ? styles.labelDummy : styles.labelNormal}`}
          >
            <Text size={200} weight="semibold">{key}:</Text>
            <Text size={200} style={{ whiteSpace: 'nowrap' }}>{value}</Text>
          </span>
        ))}
      </div>

      {/*
        Labels icon + custom-label count. Always present, anchored leftmost.
        Clicking opens a popover with the custom-label list and the add
        form — so even when every chip fits, this is still
        the canonical entry point for editing/adding labels.
      */}
      <Popover open={isPopoverOpen} onOpenChange={(_, d) => { setIsPopoverOpen(d.open); setError(''); if (!d.open) setEditingLabel(null) }}>
        <PopoverTrigger>
          <Tooltip
            content={
              <div className={styles.iconTooltipBody}>
                {`${labelEntries.length} label${labelEntries.length === 1 ? '' : 's'} — click to view or add`}
              </div>
            }
            relationship="label"
          >
            <Button
              appearance="subtle"
              size="small"
              icon={<TagRegular />}
              aria-label={`Labels (${labelEntries.length})`}
              data-testid="labels-icon-btn"
              className={styles.iconButton}
            >
              <Badge appearance="filled" size="small">{labelEntries.length}</Badge>
            </Button>
          </Tooltip>
        </PopoverTrigger>
        <PopoverSurface className={styles.popover}>
          <div className={styles.popoverSurface}>
            <Text as="h2" weight="semibold" size={300}>Default Labels</Text>
            <Text size={200}>added to new attacks and scans</Text>
            {renderLabelsList()}
            <div className={styles.popoverDivider} />
            {renderAddForm()}
          </div>
        </PopoverSurface>
      </Popover>

      <div className={styles.labelsContainer}>
        <div className={styles.metadata} ref={metadataRef}>
          <div className={styles.metadataField}>
            <Text size={200} weight="semibold">Operator:</Text>
            <div className={styles.operatorEditor}>
              <Input size="small" className={styles.operatorInput}
                value={editingLabel === 'operator' ? editValue : labels.operator ?? ''}
                readOnly={operatorReadOnly} aria-label={operatorReadOnly ? 'Signed-in operator' : 'Operator'}
                data-testid="edit-label-operator"
                onFocus={() => { if (!operatorReadOnly) handleStartEdit('operator') }}
                onChange={(_, data) => {
                  if (operatorReadOnly) return
                  if (editingLabel !== 'operator') handleStartEdit('operator')
                  setEditValue(data.value.toLowerCase())
                  setError('')
                }}
                onKeyDown={event => {
                  if (!operatorReadOnly && editingLabel === 'operator') {
                    if (event.key === 'Enter' || event.key === 'Escape') cancelPendingSave(editSession.current)
                    handleEditKeyDown(event, 'operator', editSession.current)
                  }
                }}
                onBlur={() => {
                  if (operatorReadOnly || editingLabel !== 'operator') return
                  const session = editSession.current
                  cancelPendingSave(session)
                  pendingSaves.current.set(session, setTimeout(() => {
                    pendingSaves.current.delete(session)
                    saveEdit('operator', session)
                  }, 150))
                }}
              />
              {editingLabel === 'operator' && error && <Text size={200} className={styles.errorText}>{error}</Text>}
              {editingLabel === 'operator' && (existingLabels.operator ?? [])
                .filter(value => value !== labels.operator && value.includes(editValue)).slice(0, 8).length > 0 && (
                <div className={styles.editDropdown}>
                  {(existingLabels.operator ?? []).filter(value => value !== labels.operator && value.includes(editValue))
                    .slice(0, 8).map(value => (
                      <Badge key={value} className={styles.suggestionChip} appearance="outline" size="small"
                        onMouseDown={event => event.preventDefault()}
                        onClick={() => {
                          cancelPendingSave(editSession.current)
                          commitLabels({ ...labelsRef.current, operator: value })
                          endEdit(editSession.current)
                        }}>{value}</Badge>
                    ))}
                </div>
              )}
            </div>
          </div>
          <div className={styles.metadataField} onFocus={() => {
            if (editingLabel === 'operator') {
              endEdit(editSession.current)
              editSession.current += 1
            }
          }}>
            <Text size={200} weight="semibold">Operation:</Text>
            <OperationPicker currentValue={labels.operation ?? ''} onSelect={handleSelectOperation}
              onMissing={() => handleRemoveLabel('operation')} />
            {labels.operation && <Button appearance="transparent" size="small" className={styles.removeBtn}
              icon={<DismissRegular fontSize={12} />} aria-label="Remove operation label"
              data-testid="remove-label-operation" onClick={() => handleRemoveLabel('operation')} />}
          </div>
        </div>
        {headerEntries
          .filter((_, idx) => idx < visibleCount)
          .map(([key, value], idx) => renderLabelBadge(key, value, idx))}
      </div>
    </div>
  )
}
