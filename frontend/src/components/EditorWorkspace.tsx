import type { ReactNode } from 'react'

import { Button, makeStyles, Text, tokens } from '@fluentui/react-components'

import {
  MINIMUM_TOUCH_TARGET_SIZE,
  NARROW_VIEWPORT_QUERY,
} from '@/styles/touchTargets'

export interface EditorWorkspaceItem {
  id: string
  label: string
  secondaryText?: string
}

interface EditorWorkspaceProps {
  items: EditorWorkspaceItem[]
  selectedId: string | null
  navigationLabel: string
  emptyMessage: string
  description: string
  actions?: ReactNode
  children: ReactNode
  onSelect?: (id: string) => void
}

const useStyles = makeStyles({
  root: {
    display: 'flex',
    flexDirection: 'column',
    flex: 1,
    minWidth: 0,
    minHeight: 0,
    gap: tokens.spacingVerticalM,
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    flexWrap: 'wrap',
    gap: tokens.spacingVerticalM,
  },
  workspace: {
    display: 'grid',
    gridTemplateColumns: 'minmax(12rem, 16rem) minmax(0, 1fr)',
    flex: 1,
    minWidth: 0,
    minHeight: 0,
    maxWidth: '100%',
    gap: tokens.spacingHorizontalL,
    [NARROW_VIEWPORT_QUERY]: {
      gridTemplateColumns: '1fr',
      gridTemplateRows: 'auto minmax(24rem, 1fr)',
    },
  },
  navigation: {
    display: 'flex',
    flexDirection: 'column',
    minWidth: 0,
    maxWidth: '100%',
    gap: tokens.spacingVerticalXS,
    [NARROW_VIEWPORT_QUERY]: {
      flexDirection: 'row',
      alignItems: 'flex-start',
      overflowX: 'auto',
    },
  },
  navigationButton: {
    width: '100%',
    height: 'auto',
    minHeight: MINIMUM_TOUCH_TARGET_SIZE,
    justifyContent: 'flex-start',
    padding: `${tokens.spacingVerticalS} ${tokens.spacingHorizontalM}`,
    [NARROW_VIEWPORT_QUERY]: {
      width: 'auto',
      minWidth: '10rem',
    },
  },
  navigationContent: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'flex-start',
    minWidth: 0,
    textAlign: 'left',
  },
  secondaryText: {
    display: 'block',
    maxWidth: '100%',
    overflow: 'hidden',
    color: tokens.colorNeutralForeground3,
    fontSize: tokens.fontSizeBase100,
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  editorPane: {
    display: 'flex',
    flexDirection: 'column',
    flex: 1,
    minWidth: 0,
    minHeight: 0,
    gap: tokens.spacingVerticalM,
  },
  emptyState: {
    padding: tokens.spacingVerticalXL,
    color: tokens.colorNeutralForeground3,
  },
})

export default function EditorWorkspace({
  items,
  selectedId,
  navigationLabel,
  emptyMessage,
  description,
  actions,
  children,
  onSelect,
}: EditorWorkspaceProps) {
  const styles = useStyles()

  return (
    <div className={styles.root}>
      <div className={styles.header}>
        <Text size={300}>{description}</Text>
        {actions}
      </div>
      {items.length === 0 ? (
        <div className={styles.emptyState}>{emptyMessage}</div>
      ) : (
        <div className={styles.workspace}>
          <nav className={styles.navigation} aria-label={navigationLabel}>
            {items.map((item) => (
              <Button
                key={item.id}
                className={styles.navigationButton}
                appearance={item.id === selectedId ? 'primary' : 'subtle'}
                aria-label={item.label}
                aria-current={item.id === selectedId ? 'page' : undefined}
                onClick={onSelect ? () => onSelect(item.id) : undefined}
              >
                <span className={styles.navigationContent}>
                  <span>{item.label}</span>
                  {item.secondaryText && (
                    <span className={styles.secondaryText} title={item.secondaryText}>{item.secondaryText}</span>
                  )}
                </span>
              </Button>
            ))}
          </nav>
          <div className={styles.editorPane}>{children}</div>
        </div>
      )}
    </div>
  )
}
