import { useRef } from 'react'

import Prism from 'prismjs'

import CodeEditorFrame from '@/components/CodeEditorFrame'

import { useConfigurationStyles } from './Configuration.styles'

interface DotenvEditorProps {
  value: string
  disabled: boolean
  onChange: (value: string) => void
}

Prism.languages.dotenv = {
  comment: /(^|[^\\])#.*/m,
  key: {
    pattern: /(^\s*(?:export\s+)?)[A-Za-z_][A-Za-z0-9_]*(?=\s*=)/m,
    lookbehind: true,
    alias: 'atrule',
  },
  interpolation: {
    pattern: /\$\{?[A-Za-z_][A-Za-z0-9_]*\}?/,
    alias: 'variable',
  },
  string: {
    pattern: /(^\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*\s*=\s*)(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')/m,
    lookbehind: true,
    greedy: true,
  },
  boolean: /\b(?:true|false)\b/i,
  number: /\b(?:0x[\dA-Fa-f]+|\d+(?:\.\d+)?)\b/,
  operator: /=/,
}

function highlightDotenv(value: string): string {
  return Prism.highlight(value, Prism.languages.dotenv, 'dotenv')
}

export default function DotenvEditor({ value, disabled, onChange }: DotenvEditorProps) {
  const styles = useConfigurationStyles()
  const highlightRef = useRef<HTMLPreElement>(null)

  const handleScroll = (event: React.UIEvent<HTMLTextAreaElement>): void => {
    if (highlightRef.current) {
      highlightRef.current.scrollTop = event.currentTarget.scrollTop
      highlightRef.current.scrollLeft = event.currentTarget.scrollLeft
    }
  }

  return (
    <CodeEditorFrame language="dotenv" value={value}>
      <div className={styles.editor}>
        <pre
          ref={highlightRef}
          className={styles.editorHighlight}
          aria-hidden="true"
          data-testid="dotenv-highlight"
        >
          <code dangerouslySetInnerHTML={{ __html: highlightDotenv(`${value}\n`) }} />
        </pre>
        <textarea
          className={styles.editorInput}
          aria-label="Environment file contents"
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
