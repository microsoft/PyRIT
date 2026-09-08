import { useState } from 'react'
import type { ReactNode } from 'react'

import { Button, makeStyles, Tooltip, tokens } from '@fluentui/react-components'
import { CheckmarkRegular, ClipboardRegular } from '@fluentui/react-icons'

interface CodeEditorFrameProps {
  language: string
  value: string
  children: ReactNode
}

const useStyles = makeStyles({
  root: {
    display: 'flex',
    flexDirection: 'column',
    flex: 1,
    minHeight: 0,
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    minHeight: '2rem',
    padding: `${tokens.spacingVerticalXS} ${tokens.spacingHorizontalM}`,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderBottom: 0,
    borderRadius: `${tokens.borderRadiusMedium} ${tokens.borderRadiusMedium} 0 0`,
    backgroundColor: tokens.colorNeutralBackground3,
    color: tokens.colorNeutralForeground2,
    fontSize: tokens.fontSizeBase200,
    fontWeight: tokens.fontWeightSemibold,
  },
})

export default function CodeEditorFrame({ language, value, children }: CodeEditorFrameProps) {
  const styles = useStyles()
  const [copiedValue, setCopiedValue] = useState<string | null>(null)
  const copied = copiedValue === value

  const handleCopy = async (): Promise<void> => {
    await navigator.clipboard.writeText(value)
    setCopiedValue(value)
  }

  return (
    <div className={styles.root}>
      <div className={styles.header}>
        <span>{language}</span>
        <Tooltip content={copied ? 'Copied' : `Copy ${language} source`} relationship="description">
          <Button
            appearance="transparent"
            size="small"
            icon={copied ? <CheckmarkRegular /> : <ClipboardRegular />}
            aria-label={copied ? 'Copied' : `Copy ${language} source`}
            onClick={() => void handleCopy()}
          />
        </Tooltip>
      </div>
      {children}
    </div>
  )
}
