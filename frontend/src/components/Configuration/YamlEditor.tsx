import { useRef } from 'react'

import Prism from 'prismjs'
import 'prismjs/components/prism-yaml'

import CodeEditorFrame from '@/components/CodeEditorFrame'

import { useConfigurationStyles } from './Configuration.styles'

interface YamlEditorProps {
  value: string
  disabled: boolean
  onChange: (value: string) => void
}

function highlightYaml(value: string): string {
  return Prism.highlight(value, Prism.languages.yaml, 'yaml')
}

export default function YamlEditor({ value, disabled, onChange }: YamlEditorProps) {
  const styles = useConfigurationStyles()
  const highlightRef = useRef<HTMLPreElement>(null)

  const handleScroll = (event: React.UIEvent<HTMLTextAreaElement>): void => {
    if (highlightRef.current) {
      highlightRef.current.scrollTop = event.currentTarget.scrollTop
      highlightRef.current.scrollLeft = event.currentTarget.scrollLeft
    }
  }

  return (
    <CodeEditorFrame language="YAML" value={value}>
      <div className={styles.editor}>
        <pre
          ref={highlightRef}
          className={styles.editorHighlight}
          aria-hidden="true"
          data-testid="yaml-highlight"
        >
          <code dangerouslySetInnerHTML={{ __html: highlightYaml(`${value}\n`) }} />
        </pre>
        <textarea
          className={styles.editorInput}
          aria-label="Configuration YAML"
          value={value}
          disabled={disabled}
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          onChange={(event) => onChange(event.target.value)}
          onScroll={handleScroll}
        />
      </div>
    </CodeEditorFrame>
  )
}
