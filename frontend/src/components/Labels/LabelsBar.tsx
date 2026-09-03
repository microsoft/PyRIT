import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import {
  Text,
  Button,
  Input,
  Badge,
  Combobox,
  Option,
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


const validateValue = (value: string): string | null => {
  if (!value) return 'Value is required'
  if (value !== value.toLowerCase()) return 'Values must be lowercase'
  if (!/^[a-z0-9_]+$/.test(value)) return 'Only lowercase letters, numbers, underscores'
  return null
}

const DUMMY_VALUES: Record<string, string> = {
  operator: 'roakey',
  operation: 'op_trash_panda',
}

// Fluent's listbox renders every option as a real component, so a long list
// stalls opening and typing. Past this many, you narrow the list by typing.
const MAX_LISTED = 200

interface LabelsBarProps {
  labels: Record<string, string>
  onLabelsChange: (labels: Record<string, string>) => void
}

interface OperationPickerProps {
  currentValue: string
  options: string[]
  isLoading: boolean
  loadFailed: boolean
  onSelect: (operation: string) => void
  onSearchChange: () => void
  onDismiss: () => void
  inputRef: React.Ref<HTMLInputElement>
  className?: string
  listboxClassName?: string
  noteClassName?: string
  noteErrorClassName?: string
}

/**
 * Picker for the `operation` label. Opens with every known operation listed so
 * a value can be chosen without typing, and accepts a new name via freeform entry.
 * The search text starts empty — seeding it with the current value would filter
 * the list down to nothing.
 */
function OperationPicker({
  currentValue,
  options,
  isLoading,
  loadFailed,
  onSelect,
  onSearchChange,
  onDismiss,
  inputRef,
  className,
  listboxClassName,
  noteClassName,
  noteErrorClassName,
}: OperationPickerProps) {
  const [search, setSearch] = useState('')

  // Each labels bar fetches its own list, and the popover and ribbon mount
  // separately, so a name created a moment ago may not be in `options` here.
  // List it anyway, or the picker offers to create the value already in use.
  // It goes first, whether or not the request returned it, so the cap below
  // can never be what drops it.
  // The placeholder is not a real operation, so it stays off the list.
  const listed = useMemo(() => {
    const inUse = currentValue && currentValue !== DUMMY_VALUES.operation
    return inUse ? [currentValue, ...options.filter(option => option !== currentValue)] : options
  }, [options, currentValue])

  const matches = search ? listed.filter(option => option.toLowerCase().includes(search)) : listed
  const isNewName = search.length > 0 && !listed.some(option => option.toLowerCase() === search)
  // Say why a name can't be created while it is being typed, rather than
  // rejecting it after the fact next to a bar that clips the message.
  const searchError = isNewName ? validateValue(search) : null
  const canCreate = isNewName && !searchError

  // A name typed in full has to survive the cap too. Without this, typing an
  // operation whose name is also a substring of two hundred others would leave
  // it off the list, and Enter would commit whichever one happened to be first.
  const shown = useMemo(() => {
    const exact = matches.find(option => option.toLowerCase() === search)
    const ordered = exact ? [exact, ...matches.filter(option => option !== exact)] : matches
    return ordered.slice(0, MAX_LISTED)
  }, [matches, search])

  // Deferred so focus lands on whatever the user moved to before this unmounts.
  const dismissAfterFocusMoves = () => { setTimeout(onDismiss, 0) }

  return (
    <Combobox
      ref={inputRef}
      className={className}
      size="small"
      freeform
      defaultOpen
      value={search}
      placeholder={currentValue}
      selectedOptions={listed.includes(currentValue) ? [currentValue] : []}
      onChange={e => { setSearch(e.target.value.toLowerCase()); onSearchChange() }}
      onOptionSelect={(_, data) => { if (data.optionValue) onSelect(data.optionValue) }}
      onKeyDownCapture={e => {
        // Fluent commits the active option on Tab. Block that, but let the key
        // through so focus still moves; onBlur then ends the edit.
        if (e.key === 'Tab') e.stopPropagation()
      }}
      onKeyDown={e => { if (e.key === 'Escape') onDismiss() }}
      onBlur={dismissAfterFocusMoves}
      // Fluent sizes the dropdown to the input, which cuts off longer
      // operation names, and stretches it to fill the space it has. Size to
      // content instead, and leave the height to the listbox class.
      positioning={{ matchTargetSize: undefined, autoSize: 'width' }}
      listbox={{ className: listboxClassName }}
      aria-label="Operation"
      data-testid="edit-label-operation"
    >
      {isLoading && (
        <Option disabled className={noteClassName} value="--loading" text="Loading operations">Loading operations...</Option>
      )}
      {!isLoading && loadFailed && (
        <Option disabled className={noteErrorClassName} value="--failed" text="Could not load operations">
          Could not load existing operations — type a name to use one
        </Option>
      )}
      {!isLoading && !loadFailed && listed.length === 0 && !canCreate && !searchError && (
        <Option disabled className={noteClassName} value="--empty" text="No operations">
          No operations yet — type a name to create one
        </Option>
      )}
      {shown.map(option => (
        <Option key={option} value={option}>{option}</Option>
      ))}
      {matches.length > MAX_LISTED && (
        <Option disabled className={noteClassName} value="--more" text="Type to narrow">
          {`Showing ${MAX_LISTED} of ${matches.length} — type to narrow`}
        </Option>
      )}
      {canCreate && (
        <Option key="__create" value={search} text={search}>{`Create "${search}"`}</Option>
      )}
      {searchError && (
        <Option disabled className={noteErrorClassName} key="__invalid" value="--invalid" text={searchError}>
          {searchError}
        </Option>
      )}
    </Combobox>
  )
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
  const [labelsLoading, setLabelsLoading] = useState(true)
  const [labelsFailed, setLabelsFailed] = useState(false)
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
        operation: [...new Set([...(resp.labels.operation || []), ...(prev.operation || [])])],
      })))
      .catch(() => setLabelsFailed(true))
      .finally(() => setLabelsLoading(false))
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
    // Don't allow removing operator or operation — they're required
    if (key === 'operator' || key === 'operation') return
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
    editSession.current += 1
    setEditingLabel(key)
    setEditValue(labels[key])
    setError('')
    setTimeout(() => editInputRef.current?.focus(), 50)
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
    const known = existingLabels.operation || []
    // Values already in memory predate the current rules, and so may the one
    // already in use, so both are always selectable; only a newly typed name
    // has to satisfy them.
    const inUse = operation === labels.operation
    if (!known.includes(operation) && !inUse) {
      const valueError = validateValue(operation)
      if (valueError) { setError(valueError); return }
      // A name only reaches the labels API once an attack has been stored under
      // it, so keep it listed here or the picker forgets what it just created.
      setExistingLabels(prev => ({
        ...prev,
        operation: [...(prev.operation || []), operation],
      }))
    }
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

  const renderValueEditor = (key: string, value: string) => {
    // Whatever is deferred below belongs to this edit, and only this one.
    const session = editSession.current
    if (key === 'operation') {
      return (
        <>
          <Text size={200} weight="semibold">{key}:</Text>
          <OperationPicker
            className={styles.operationPicker}
            listboxClassName={styles.operationListbox}
            noteClassName={styles.operationNote}
            noteErrorClassName={styles.operationNoteError}
            currentValue={value}
            options={suggestedValues}
            isLoading={labelsLoading}
            loadFailed={labelsFailed}
            onSelect={handleSelectOperation}
            onSearchChange={() => setError('')}
            onDismiss={() => endEdit(session)}
            inputRef={editInputRef}
          />
          {error && <Text size={200} className={styles.errorText}>{error}</Text>}
        </>
      )
    }

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
    const isRequired = key === 'operator' || key === 'operation'
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
          content={isDummy ? `Placeholder value — click to change` : `Click to edit`}
          relationship="description"
        >
          <div
            className={styles.labelEdit}
            onClick={() => handleStartEdit(key)}
            onKeyDown={e => handleStartEditKeyDown(e, key)}
            role="button"
            tabIndex={0}
            aria-label={`Edit ${key} label, currently ${value}`}
            data-testid={`label-${key}`}
          >
            <Text size={200} weight="semibold">{key}:</Text>
            <Text size={200} style={{ whiteSpace: 'nowrap' }}>{value}</Text>
          </div>
        </Tooltip>
        {!isRequired && (
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

  const renderLabelsList = () => (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
      {labelEntries.map(([key, value]) => {
        const isDummy = isDummyValue(key, value)
        const isRequired = key === 'operator' || key === 'operation'
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
            className={`${styles.labelBadge} ${isDummy ? styles.labelDummy : styles.labelNormal}`}
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
            {!isRequired && (
              <Button
                className={styles.removeBtn}
                appearance="transparent"
                size="small"
                icon={<DismissRegular fontSize={12} />}
                onClick={(e) => { e.stopPropagation(); handleRemoveLabel(key) }}
                aria-label={`Remove ${key} label`}
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
      {error && !editingLabel && <Text size={200} className={styles.errorText}>{error}</Text>}
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
