import type { DragEventHandler, ReactNode } from 'react'

import {
  Button, Menu, MenuTrigger, MenuPopover, MenuList, MenuItem, Text,
} from '@fluentui/react-components'
import { DismissRegular, MoreHorizontalRegular, ReOrderDotsVerticalRegular } from '@fluentui/react-icons'

import type { ConverterInstance } from '@/types'

import { useConverterCardStyles } from './ConverterCard.styles'

interface ConverterCardProps {
  name: string
  converter?: ConverterInstance
  temporary?: boolean
  disabled?: boolean
  context?: string
  testId?: string
  dragTestId?: string
  onRemove: () => void
  onSettings?: () => void
  onReset?: () => void
  onMove: (delta: number) => void
  onDragStart?: DragEventHandler<HTMLDivElement>
  onDragEnd?: DragEventHandler<HTMLDivElement>
  onDragOver?: DragEventHandler<HTMLDivElement>
  onDrop?: DragEventHandler<HTMLDivElement>
  children?: ReactNode
}

/** Converter stage shared by single-message, bulk conversation, and technique editing. */
export default function ConverterCard({
  name, converter, temporary, disabled = false, context = '', testId, dragTestId,
  onRemove, onSettings, onReset, onMove, onDragStart, onDragEnd, onDragOver, onDrop, children,
}: ConverterCardProps) {
  const styles = useConverterCardStyles()
  return <div className={styles.root} data-testid={testId} onDragOver={onDragOver} onDrop={onDrop}>
    <div className={styles.header} draggable={!disabled} data-testid={dragTestId}
      onDragStart={(event) => {
        if ((event.target as HTMLElement).closest('[data-no-drag]') || disabled) {
          event.preventDefault()
          return
        }
        onDragStart?.(event)
      }} onDragEnd={onDragEnd}>
      <Button appearance="subtle" size="small" icon={<ReOrderDotsVerticalRegular />}
        className={styles.dragHandle} disabled={disabled}
        aria-label={`Reorder converter ${name}${context}`}
        title="Drag this header to reorder. Use the arrow keys for keyboard reordering."
        onKeyDown={(event) => {
          if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
            event.preventDefault()
            onMove(event.key === 'ArrowUp' ? -1 : 1)
          }
        }} />
      <Text weight="semibold" size={300} className={styles.name}>{name}</Text>
      {converter?.is_llm_based && <span className={styles.llmBadge}>LLM</span>}
      {temporary && <Text size={200}>Temporary</Text>}
      <Button appearance="subtle" size="small" icon={<DismissRegular />} data-no-drag
        className={styles.remove} disabled={disabled}
        aria-label={`Remove converter ${name}${context}`} onClick={onRemove} />
    </div>
    {converter?.identifier.class_name !== name && <Text size={200} className={styles.hint}>
      {converter?.identifier.class_name}
    </Text>}
    <Text size={200} className={styles.hint}>
      {converter ? converter.description || 'No description is available.' : 'This converter is no longer registered.'}
    </Text>
    {children}
    {onSettings && <div className={styles.footer}><Menu>
      <MenuTrigger disableButtonEnhancement>
        <Button appearance="subtle" size="small" icon={<MoreHorizontalRegular />} className={styles.settings}
          aria-label={`Settings for converter ${name}${context}`} disabled={disabled} />
      </MenuTrigger>
      <MenuPopover><MenuList>
        <MenuItem disabled={!converter?.reconstructable}
          title={converter?.reconstructable ? undefined
            : converter?.reconstruction_error ?? 'This converter does not support separate settings.'}
          onClick={onSettings}>Settings</MenuItem>
        {temporary && onReset && <MenuItem onClick={onReset}>Reset to registered converter</MenuItem>}
      </MenuList></MenuPopover>
    </Menu></div>}
  </div>
}
