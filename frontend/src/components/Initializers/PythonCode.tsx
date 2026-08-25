import { useRef, useState } from 'react'

import { Button, Tooltip } from '@fluentui/react-components'
import { CheckmarkRegular, ClipboardRegular } from '@fluentui/react-icons'
import Prism from 'prismjs'
import 'prismjs/components/prism-python'

import { usePythonCodeStyles } from './PythonCode.styles'

interface PythonCodeBlockProps {
  source: string
  ariaLabel: string
}

interface PythonCodeEditorProps {
  source: string
  disabled: boolean
  onChange: (source: string) => void
}

interface CodeHeaderProps {
  source: string
}

function highlightPython(source: string): string {
  return Prism.highlight(source, Prism.languages.python, 'python')
}

function CodeHeader({ source }: CodeHeaderProps) {
  const styles = usePythonCodeStyles()
  const [copiedSource, setCopiedSource] = useState<string | null>(null)
  const copied = copiedSource === source

  const handleCopy = async (): Promise<void> => {
    await navigator.clipboard.writeText(source)
    setCopiedSource(source)
  }

  return (
    <div className={styles.codeHeader}>
      <span>Python</span>
      <Tooltip content={copied ? 'Copied' : 'Copy Python source'} relationship="description">
        <Button
          appearance="transparent"
          size="small"
          icon={copied ? <CheckmarkRegular /> : <ClipboardRegular />}
          aria-label={copied ? 'Copied' : 'Copy Python source'}
          onClick={() => void handleCopy()}
        />
      </Tooltip>
    </div>
  )
}

export function PythonCodeBlock({ source, ariaLabel }: PythonCodeBlockProps) {
  const styles = usePythonCodeStyles()

  return (
    <div>
      <CodeHeader source={source} />
      <pre className={styles.codeBlock} aria-label={ariaLabel}>
        <code dangerouslySetInnerHTML={{ __html: highlightPython(source) }} />
      </pre>
    </div>
  )
}

export function PythonCodeEditor({ source, disabled, onChange }: PythonCodeEditorProps) {
  const styles = usePythonCodeStyles()
  const highlightRef = useRef<HTMLPreElement>(null)

  const handleScroll = (event: React.UIEvent<HTMLTextAreaElement>): void => {
    if (highlightRef.current) {
      highlightRef.current.scrollTop = event.currentTarget.scrollTop
      highlightRef.current.scrollLeft = event.currentTarget.scrollLeft
    }
  }

  return (
    <div>
      <CodeHeader source={source} />
      <div className={styles.editor}>
        <pre ref={highlightRef} className={`${styles.codeBlock} ${styles.editorHighlight}`} aria-hidden="true">
          <code dangerouslySetInnerHTML={{ __html: highlightPython(`${source}\n`) }} />
        </pre>
        <textarea
          className={styles.editorInput}
          aria-label="Python source"
          value={source}
          onChange={(event) => onChange(event.target.value)}
          onScroll={handleScroll}
          disabled={disabled}
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
        />
      </div>
    </div>
  )
}