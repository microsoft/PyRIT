import type { ChangeEvent } from 'react'

import { ChevronDownRegular } from '@fluentui/react-icons'

import type { TargetInstance } from '@/types'
import { targetModelName } from '@/utils/targetIdentity'

import TargetBadge from './TargetBadge'
import { useChatTargetPickerStyles } from './ChatTargetPicker.styles'

interface ChatTargetPickerProps {
  target: TargetInstance | null
  targets: TargetInstance[]
  loading: boolean
  error: string | null
  disabled: boolean
  onSelect: (target: TargetInstance | null) => void
}

export default function ChatTargetPicker({
  target, targets, loading, error, disabled, onSelect,
}: ChatTargetPickerProps) {
  const styles = useChatTargetPickerStyles()
  const placeholder = loading ? 'Loading targets...'
    : error ? 'Targets unavailable'
    : targets.length === 0 ? 'No targets registered' : 'Select a target'
  const picker = (
    <>
      <ChevronDownRegular aria-hidden="true" />
      <select
        className={styles.select}
        aria-label="Chat target"
        value={target?.target_registry_name ?? ''}
        disabled={disabled || loading || Boolean(error) || targets.length === 0}
        onChange={(event: ChangeEvent<HTMLSelectElement>) => {
          onSelect(targets.find((item: TargetInstance) => item.target_registry_name === event.target.value) ?? null)
        }}
      >
        <option value="">{placeholder}</option>
        {targets.map((item: TargetInstance) => (
          <option key={item.target_registry_name} value={item.target_registry_name}>
            {item.target_registry_name}{targetModelName(item) ? ` (${targetModelName(item)})` : ''}
          </option>
        ))}
      </select>
    </>
  )

  return (
    <span className={styles.root} data-tour="chat-prerequisite">
      <TargetBadge target={target} picker={picker} emptyLabel={placeholder} />
    </span>
  )
}
