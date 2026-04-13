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
  AddRegular,
  DismissRegular,
  WarningRegular,
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

  // Overflow detection: track which labels are visible
  const containerRef = useRef<HTMLDivElement>(null)
  const [visibleCount, setVisibleCount] = useState(Infinity)

  const labelEntries = useMemo(() => Object.entries(labels), [labels])

  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    const check = () => {
      const children = Array.from(el.children) as HTMLElement[]
      if (children.length === 0) { setVisibleCount(Infinity); return }

      const containerRight = el.getBoundingClientRect().right
      let count = 0
      for (const child of children) {
        // Skip the overflow badge and add button (last elements)
        if (child.dataset.labelIdx === undefined) continue
        if (child.getBoundingClientRect().right <= containerRight + 2) {
          count++
        } else {
          break
        }
      }
      setVisibleCount(count)
    }

    const observer = new ResizeObserver(check)
    observer.observe(el)
    check()
    return () => observer.disconnect()
  }, [labelEntries])

  const overflowEntries = visibleCount < labelEntries.length
    ? labelEntries.slice(visibleCount)
    : []

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

  return (
    <div className={styles.root} data-testid="labels-bar">
      {hasDummyValues && (
        <Tooltip content="Some labels have placeholder values — update them for proper tracking" relationship="description">
          <span className={styles.warningIcon} data-testid="labels-warning">
            <WarningRegular fontSize={16} />
          </span>
        </Tooltip>
      )}

      <div className={styles.labelsContainer} ref={containerRef}>
        {labelEntries.map(([key, value], idx) => renderLabelBadge(key, value, idx))}

        {overflowEntries.length > 0 && (
          <Popover>
            <PopoverTrigger>
              <Badge
                appearance="outline"
                size="small"
                className={styles.overflowBadge}
                data-testid="labels-overflow"
              >
                +{overflowEntries.length} more
              </Badge>
            </PopoverTrigger>
            <PopoverSurface>
              <div className={styles.popoverSurface}>
                <Text weight="semibold" size={300}>All Labels</Text>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                  {labelEntries.map(([key, value]) => {
                    const isDummy = isDummyValue(key, value)
                    const isRequired = key === 'operator' || key === 'operation'
                    return (
                      <div
                        key={key}
                        className={`${styles.labelBadge} ${isDummy ? styles.labelDummy : styles.labelNormal}`}
                        onClick={() => handleStartEdit(key)}
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
                          />
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            </PopoverSurface>
          </Popover>
        )}
      </div>

      <Popover open={isPopoverOpen} onOpenChange={(_, d) => { setIsPopoverOpen(d.open); setError('') }}>
        <PopoverTrigger>
          <Button
            appearance="subtle"
            size="small"
            icon={<AddRegular />}
            data-testid="add-label-btn"
            style={{ flexShrink: 0 }}
          >
            Add
          </Button>
          </PopoverTrigger>
          <PopoverSurface>
            <div className={styles.popoverSurface}>
              <Text weight="semibold" size={300}>Add Label</Text>
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
            </div>
          </PopoverSurface>
        </Popover>
    </div>
  )
}
