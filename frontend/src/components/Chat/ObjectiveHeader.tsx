import { useEffect, useLayoutEffect, useRef, useState } from 'react'

import {
  Badge,
  Button,
  Input,
  MessageBar,
  MessageBarBody,
  Text,
  mergeClasses,
} from '@fluentui/react-components'
import { AddRegular, ChevronDownRegular, ChevronUpRegular } from '@fluentui/react-icons'

import { useObjectiveHeaderStyles } from './ObjectiveHeader.styles'

interface ObjectiveHeaderProps {
  objective: string
  canAdd?: boolean
  onAdd?: (objective: string) => Promise<void>
  editRequestId?: number
}

export default function ObjectiveHeader({ objective, canAdd = false, onAdd, editRequestId = 0 }: ObjectiveHeaderProps) {
  const styles = useObjectiveHeaderStyles()
  const [expanded, setExpanded] = useState(false)
  const [overflowing, setOverflowing] = useState(false)
  const [isEditing, setIsEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [isSaving, setIsSaving] = useState(false)
  const [error, setError] = useState('')
  const [showManualScoreWarning, setShowManualScoreWarning] = useState(false)
  const contentRef = useRef<HTMLElement>(null)

  useEffect(() => {
    if (editRequestId > 0 && !objective && onAdd) {
      setError('')
      setShowManualScoreWarning(true)
      setIsEditing(true)
    }
  }, [editRequestId, objective, onAdd])

  useLayoutEffect(() => {
    const content = contentRef.current
    if (!content) return

    const measure = () => {
      if (expanded) return
      setOverflowing(content.scrollWidth > content.clientWidth)
    }

    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(content)
    return () => observer.disconnect()
  }, [objective, expanded])

  const handleSave = async (): Promise<void> => {
    const trimmedObjective = draft.trim()
    if (!trimmedObjective || !onAdd) return

    setIsSaving(true)
    setError('')
    try {
      await onAdd(trimmedObjective)
      setIsEditing(false)
      setShowManualScoreWarning(false)
      setDraft('')
    } catch {
      setError('Unable to save the objective.')
    } finally {
      setIsSaving(false)
    }
  }

  if (!objective) {
    if ((!canAdd && !isEditing) || !onAdd) return null
    return (
      <>
        {showManualScoreWarning && (
          <MessageBar intent="warning" data-testid="manual-score-objective-warning">
            <MessageBarBody>
              An objective is required before you can add a manual score. Enter and save an objective, then click
              Add manual score again.
            </MessageBarBody>
          </MessageBar>
        )}
        <div className={styles.root} data-testid="objective-header">
          <Badge className={styles.label} appearance="tint" color="brand" size="small">
            Objective
          </Badge>
          {isEditing ? (
            <>
              <Input
                className={styles.input}
                value={draft}
                onChange={(_event, data) => setDraft(data.value)}
                placeholder="Enter an objective"
                aria-label="Attack objective"
                autoFocus
              />
              <Button appearance="primary" size="small" onClick={handleSave} disabled={!draft.trim() || isSaving}>
                {isSaving ? 'Saving...' : 'Save'}
              </Button>
              <Button
                appearance="subtle"
                size="small"
                onClick={() => {
                  setIsEditing(false)
                  setShowManualScoreWarning(false)
                }}
                disabled={isSaving}
              >
                Cancel
              </Button>
              {error && <Text role="alert">{error}</Text>}
            </>
          ) : (
            <Button
              appearance="subtle"
              size="small"
              icon={<AddRegular />}
              onClick={() => {
                setShowManualScoreWarning(false)
                setIsEditing(true)
              }}
              className={styles.addButton}
            >
              Add objective
            </Button>
          )}
        </div>
      </>
    )
  }

  const showToggle = overflowing || expanded

  return (
    <div className={styles.root} data-testid="objective-header">
      <Badge className={styles.label} appearance="tint" color="brand" size="small">
        Objective
      </Badge>
      <Text
        ref={contentRef}
        className={mergeClasses(styles.content, expanded ? styles.contentExpanded : styles.contentCollapsed)}
        data-testid="objective-header-content"
      >
        {objective}
      </Text>
      {showToggle && (
        <Button
          appearance="transparent"
          size="small"
          icon={expanded ? <ChevronUpRegular /> : <ChevronDownRegular />}
          iconPosition="after"
          onClick={() => setExpanded((previous: boolean) => !previous)}
          className={styles.toggle}
          data-testid="toggle-objective-header-btn"
          aria-expanded={expanded}
          aria-label={expanded ? 'Show less of the objective' : 'Show more of the objective'}
        >
          {expanded ? 'Show less' : 'Show more'}
        </Button>
      )}
    </div>
  )
}
