import { useRef } from 'react'

import Prism from 'prismjs'
import 'prismjs/components/prism-python'

import CodeEditorFrame from '@/components/CodeEditorFrame'

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

function highlightPython(source: string): string {
  return Prism.highlight(source, Prism.languages.python, 'python')
}

export function PythonCodeBlock({ source, ariaLabel }: PythonCodeBlockProps) {
  const styles = usePythonCodeStyles()

  return (
    <CodeEditorFrame language="Python" value={source}>
      <pre className={styles.codeBlock} aria-label={ariaLabel}>
        <code dangerouslySetInnerHTML={{ __html: highlightPython(source) }} />
      </pre>
    </CodeEditorFrame>
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
    <CodeEditorFrame language="Python" value={source}>
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
    </CodeEditorFrame>
  )
}
