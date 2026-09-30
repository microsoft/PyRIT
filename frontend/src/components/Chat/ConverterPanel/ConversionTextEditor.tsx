import { useRef, useState } from 'react'
import type { ChangeEvent, ReactNode, SyntheticEvent, UIEvent } from 'react'

import { Button, MessageBar, MessageBarBody } from '@fluentui/react-components'

import { useConverterPanelStyles } from './ConverterPanel.styles'

const START_MARKER = '\u27ea'
const END_MARKER = '\u27eb'

function sourceOffset(value: string, textareaOffset: number): number {
  let offset = 0
  for (let index = 0; index < textareaOffset && offset < value.length; index++) {
    // Textareas count CRLF as one character; keep the source line endings intact.
    offset += value[offset] === '\r' && value[offset + 1] === '\n' ? 2 : 1
  }
  return offset
}

function highlightMarkedRegions(value: string): ReactNode[] {
  const parts: ReactNode[] = []
  let cursor = 0
  while (cursor < value.length) {
    const start = value.indexOf(START_MARKER, cursor)
    if (start < 0) {
      parts.push(value.slice(cursor))
      break
    }
    const end = value.indexOf(END_MARKER, start + START_MARKER.length)
    if (end < 0) {
      parts.push(value.slice(cursor))
      break
    }
    parts.push(value.slice(cursor, start))
    parts.push(
      <mark key={`${start}-${end}`} data-testid="conversion-marked-region">
        {value.slice(start, end + END_MARKER.length)}
      </mark>,
    )
    cursor = end + END_MARKER.length
  }
  return parts
}

interface ConversionTextEditorProps {
  readonly value: string
  readonly label: string
  readonly placeholder: string
  readonly allowSelection: boolean
  readonly onChange: (value: string) => void
}

export default function ConversionTextEditor({
  value,
  label,
  placeholder,
  allowSelection,
  onChange,
}: ConversionTextEditorProps) {
  const styles = useConverterPanelStyles()
  const textarea = useRef<HTMLTextAreaElement>(null)
  const highlightLayer = useRef<HTMLPreElement>(null)
  const [selection, setSelection] = useState({ start: 0, end: 0, value })
  const [error, setError] = useState<string | null>(null)
  const editorValue = value.replace(/\r\n?/g, '\n')

  const rememberSelection = (event: SyntheticEvent<HTMLTextAreaElement>): void => {
    if (event.currentTarget.value !== editorValue) return
    setSelection({
      start: sourceOffset(value, event.currentTarget.selectionStart),
      end: sourceOffset(value, event.currentTarget.selectionEnd),
      value,
    })
  }

  const markSelection = (): void => {
    const { start, end } = selection
    const selected = value.slice(start, end)
    const before = value.slice(0, start)
    if (!selected || selection.value !== value) return
    if (
      selected.includes(START_MARKER) || selected.includes(END_MARKER)
      || before.lastIndexOf(START_MARKER) > before.lastIndexOf(END_MARKER)
    ) {
      setError('Select text outside an existing marked region.')
      return
    }
    setError(null)
    onChange(`${before}${START_MARKER}${selected}${END_MARKER}${value.slice(end)}`)
    setSelection({ start: 0, end: 0, value })
    textarea.current?.focus()
  }

  const synchronizeScroll = (event: UIEvent<HTMLTextAreaElement>): void => {
    if (!highlightLayer.current) return
    highlightLayer.current.scrollTop = event.currentTarget.scrollTop
    highlightLayer.current.scrollLeft = event.currentTarget.scrollLeft
  }

  return (
    <div className={styles.textEditor}>
      <div className={styles.highlightEditor}>
        <pre
          ref={highlightLayer}
          className={styles.highlightLayer}
          aria-hidden="true"
          data-testid="conversion-highlight-layer"
        >
          {highlightMarkedRegions(editorValue)}
          {editorValue.endsWith('\n') ? '\n' : ''}
        </pre>
        <textarea
          ref={textarea}
          value={editorValue}
          aria-label={label}
          placeholder={placeholder}
          rows={4}
          className={styles.editableValue}
          onSelect={rememberSelection}
          onKeyUp={rememberSelection}
          onScroll={synchronizeScroll}
          onChange={(event: ChangeEvent<HTMLTextAreaElement>): void => {
            setError(null)
            onChange(event.target.value)
          }}
        />
      </div>
      {allowSelection && (
        <Button
          size="small"
          appearance="subtle"
          className={styles.selectionButton}
          disabled={selection.start === selection.end || selection.value !== value}
          aria-label={`Convert selection only in ${label}`}
          title="Convert only the marked text in the next stage. Later stages use the whole result."
          onClick={markSelection}
        >
          Convert selection only
        </Button>
      )}
      {error && (
        <MessageBar intent="error">
          <MessageBarBody>{error}</MessageBarBody>
        </MessageBar>
      )}
    </div>
  )
}
