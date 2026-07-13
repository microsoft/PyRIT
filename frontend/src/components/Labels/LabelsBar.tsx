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
import { labelsApi } from '../../services/api'
import { useLabelsBarStyles } from './LabelsBar.styles'


const DUMMY_VALUES: Record<string, string> = {
  operator: 'roakey',
  operation: 'op_trash_panda',
}

interface LabelsBarProps {
  labels: Record<string, string>
  onLabelsChange: (labels: Record<string, string>) => void
}

export default function LabelsBar({ labels, onLabelsChange }: LabelsBarProps) {
  const styles = useLabelsBarStyles()
  const [isPopoverOpen, setIsPopoverOpen] = useState(false)
  const [newKey, setNewKey] = useState('')
  const [newValue, setNewValue] = useState('')
  const [editingLabel, setEditingLabel] = useState<string | null>(null)
  const [editValue, setEditValue] = useState('')
  const [error, setError] = useState('')
  const [existingLabels, setExistingLabels] = useState<Record<string, string[]>>({})
  const editInputRef = useRef<HTMLInputElement>(null)

  // Fetch existing label keys/values for suggestions
  useEffect(() => {
    labelsApi.getLabels()
      .then(resp => setExistingLabels(resp.labels))
      .catch(() => { /* ignore */ })
  }, [])

  const isDummyValue = useCallback((key: string, value: string): boolean => {
    return DUMMY_VALUES[key] === value
  }, [])

  const hasDummyValues = Object.entries(labels).some(([k, v]) => isDummyValue(k, v))

  const validateKey = (key: string): string | null => {
    if (!key) return 'Key is required'
    if (key !== key.toLowerCase()) return 'Labels must be lowercase'
    if (!/^[a-z][a-z0-9_]*$/.test(key)) return 'Only lowercase letters, numbers, underscores'
    if (key in labels) return 'Label key already exists'
    return null
  }

  const validateValue = (value: string): string | null => {
    if (!value) return 'Value is required'
    if (value !== value.toLowerCase()) return 'Values must be lowercase'
    if (!/^[a-z0-9_]+$/.test(value)) return 'Only lowercase letters, numbers, underscores'
    return null
  }

  const handleAddLabel = () => {
    const keyError = validateKey(newKey)
    if (keyError) { setError(keyError); return }
    const valueError = validateValue(newValue)
    if (valueError) { setError(valueError); return }

    onLabelsChange({ ...labels, [newKey]: newValue })
    setNewKey('')
    setNewValue('')
    setError('')
    setIsPopoverOpen(false)
  }

  const handleRemoveLabel = (key: string) => {
    // Don't allow removing operator or operation — they're required
    if (key === 'operator' || key === 'operation') return
    const next = { ...labels }
    delete next[key]
    onLabelsChange(next)
  }

  const handleStartEdit = (key: string) => {
    setEditingLabel(key)
    setEditValue(labels[key])
    setError('')
    setTimeout(() => editInputRef.current?.focus(), 50)
  }

  const handleSaveEdit = () => {
    if (!editingLabel) return
    const valueError = validateValue(editValue)
    if (valueError) { setError(valueError); return }
    onLabelsChange({ ...labels, [editingLabel]: editValue })
    setEditingLabel(null)
    setEditValue('')
    setError('')
  }

  const handleEditKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') handleSaveEdit()
    if (e.key === 'Escape') { setEditingLabel(null); setError('') }
  }

  const handleAddKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') handleAddLabel()
    if (e.key === 'Escape') setIsPopoverOpen(false)
  }

  // Suggestions: show existing keys not yet used, and values for the current key
  const suggestedKeys = Object.keys(existingLabels).filter(k => !(k in labels))
  const suggestedValues = (editingLabel ? existingLabels[editingLabel] : existingLabels[newKey]) || []

  // Layout: the labels icon (with total count badge) is always the first
  // element on the bar. We then render as many full chips as fit, in
  // declaration order. The icon's popover always shows the full list and
  // the add form, so the user can reach everything regardless of how many
  // chips are currently visible. The inline "+ Add" button is only shown
  // when every chip already fits — otherwise the popover already covers
  // the same flow.
  const rootRef = useRef<HTMLDivElement>(null)
  const measureRef = useRef<HTMLDivElement>(null)
  const ICON_BUTTON_WIDTH_PX = 56  // labels icon + count badge + gap
  const ADD_BUTTON_WIDTH_PX = 60   // "+ Add" button
  const [visibleCount, setVisibleCount] = useState(Infinity)

  const labelEntries = useMemo(() => Object.entries(labels), [labels])

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
      const reserved = ICON_BUTTON_WIDTH_PX + gap
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
    if (root.parentElement) observer.observe(root.parentElement)
    check()
    return () => observer.disconnect()
  }, [labelEntries])

  const renderLabelBadge = (key: string, value: string, idx: number) => {
    const isDummy = isDummyValue(key, value)
    const isRequired = key === 'operator' || key === 'operation'
    const isEditing = editingLabel === key

    if (isEditing) {
      const filteredSuggestions = suggestedValues
        .filter(v => v !== value && v.includes(editValue))
        .slice(0, 8)
      return (
        <div key={key} data-label-idx={idx} className={styles.inputRow} style={{ display: 'inline-flex', position: 'relative', flexShrink: 0 }}>
          <Text size={200} weight="semibold">{key}:</Text>
          <Input
            ref={editInputRef}
            size="small"
            value={editValue}
            onChange={(_, d) => { setEditValue(d.value.toLowerCase()); setError('') }}
            onKeyDown={handleEditKeyDown}
            onBlur={() => { setTimeout(handleSaveEdit, 150) }}
            style={{ width: '120px' }}
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
                  onClick={() => { onLabelsChange({ ...labels, [key]: v }); setEditingLabel(null); setEditValue('') }}
                >{v}</Badge>
              ))}
            </div>
          )}
        </div>
      )
    }

    return (
      <Tooltip
        key={key}
        content={isDummy ? `Placeholder value — click to change` : `Click to edit`}
        relationship="description"
      >
        <div
          data-label-idx={idx}
          className={`${styles.labelBadge} ${isDummy ? styles.labelDummy : styles.labelNormal}`}
          onClick={() => handleStartEdit(key)}
          data-testid={`label-${key}`}
          style={{ flexShrink: 0 }}
        >
          <Text size={200} weight="semibold">{key}:</Text>
          <Text size={200} style={{ whiteSpace: 'nowrap' }}>{value}</Text>
          {!isRequired && (
            <Button
              className={styles.removeBtn}
              appearance="transparent"
              size="small"
              icon={<DismissRegular fontSize={12} />}
              onClick={(e) => { e.stopPropagation(); handleRemoveLabel(key) }}
              data-testid={`remove-label-${key}`}
            />
          )}
        </div>
      </Tooltip>
    )
  }

  const renderLabelsList = () => (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
      {labelEntries.map(([key, value]) => {
        const isDummy = isDummyValue(key, value)
        const isRequired = key === 'operator' || key === 'operation'
        return (
          <div
            key={key}
            className={`${styles.labelBadge} ${isDummy ? styles.labelDummy : styles.labelNormal}`}
            onClick={() => handleStartEdit(key)}
            data-testid={`popover-label-${key}`}
            style={{ flexShrink: 0 }}
          >
            <Text size={200} weight="semibold">{key}:</Text>
            <Text size={200}>{value}</Text>
            {!isRequired && (
              <Button
                className={styles.removeBtn}
                appearance="transparent"
                size="small"
                icon={<DismissRegular fontSize={12} />}
                onClick={(e) => { e.stopPropagation(); handleRemoveLabel(key) }}
                data-testid={`popover-remove-label-${key}`}
              />
            )}
          </div>
        )
      })}
    </div>
  )

  const renderAddForm = () => (
    <>
      <div className={styles.inputRow}>
        <Input
          className={styles.inputField}
          size="small"
          placeholder="key"
          value={newKey}
          onChange={(_, d) => { setNewKey(d.value.toLowerCase()); setError('') }}
          onKeyDown={handleAddKeyDown}
          data-testid="new-label-key"
        />
        <Input
          className={styles.inputField}
          size="small"
          placeholder="value"
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
      {error && <Text size={200} className={styles.errorText}>{error}</Text>}
    </>
  )

  return (
    <div className={styles.root} data-testid="labels-bar" ref={rootRef}>
      {hasDummyValues && (
        <Tooltip content="Some labels have placeholder values — update them for proper tracking" relationship="description">
          <span className={styles.warningIcon} data-testid="labels-warning">
            <WarningRegular fontSize={16} />
          </span>
        </Tooltip>
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
        {labelEntries.map(([key, value], idx) => (
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
        Labels icon + total count. Always present, anchored leftmost.
        Clicking opens a popover with the full label list and add form
        — so even when every chip fits, this is still the canonical
        entry point for editing/adding labels.
      */}
      <Popover open={isPopoverOpen} onOpenChange={(_, d) => { setIsPopoverOpen(d.open); setError('') }}>
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
        <PopoverSurface>
          <div className={styles.popoverSurface}>
            <Text weight="semibold" size={300}>All Labels</Text>
            {renderLabelsList()}
            <div className={styles.popoverDivider} />
            <Text weight="semibold" size={300}>Add Label</Text>
            {renderAddForm()}
          </div>
        </PopoverSurface>
      </Popover>

      <div className={styles.labelsContainer}>
        {labelEntries
          .slice(0, visibleCount === Infinity ? labelEntries.length : visibleCount)
          .map(([key, value], idx) => renderLabelBadge(key, value, idx))}
      </div>
    </div>
  )
}
