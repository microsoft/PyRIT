import { useLayoutEffect, useRef, useState } from 'react'

import {
  Badge,
  Button,
  Field,
  Input,
  Popover,
  PopoverSurface,
  PopoverTrigger,
  Radio,
  RadioGroup,
  Text,
  Textarea,
  mergeClasses,
} from '@fluentui/react-components'
import {
  AddRegular,
  ChevronDownRegular,
  ChevronUpRegular,
  InfoRegular,
} from '@fluentui/react-icons'

import OutcomeBadge from '@/components/OutcomeBadge'
import type { AttackOutcome, BackendScore } from '@/types'

import { useObjectiveHeaderStyles } from './ObjectiveHeader.styles'

interface ObjectiveHeaderProps {
  objective: string
  outcome?: AttackOutcome
  automatedScore?: BackendScore | null
  humanScore?: BackendScore | null
  canUpdateOutcome?: boolean
  canRemoveHumanScore?: boolean
  onUpdateHumanScore?: (value: boolean, rationale: string) => Promise<void>
  onRemoveHumanScore?: () => Promise<void>
  canAdd?: boolean
  onAdd?: (objective: string) => Promise<void>
}

function scoreVerdict(score?: BackendScore | null): 'success' | 'failure' | 'undetermined' {
  if (!score?.score_value) return 'undetermined'
  return score.score_value.toLowerCase() === 'true' ? 'success' : 'failure'
}

function scoreLabel(score?: BackendScore | null): string {
  const verdict = scoreVerdict(score)
  return verdict === 'undetermined' ? 'Not set' : verdict === 'success' ? 'Success' : 'Failure'
}

export default function ObjectiveHeader({
  objective,
  outcome,
  automatedScore,
  humanScore,
  canUpdateOutcome = false,
  canRemoveHumanScore = canUpdateOutcome,
  onUpdateHumanScore,
  onRemoveHumanScore,
  canAdd = false,
  onAdd,
}: ObjectiveHeaderProps) {
  const styles = useObjectiveHeaderStyles()
  const [expanded, setExpanded] = useState(false)
  const [overflowing, setOverflowing] = useState(false)
  const [isEditing, setIsEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [isSaving, setIsSaving] = useState(false)
  const [error, setError] = useState('')
  const initialVerdict = scoreVerdict(humanScore ?? automatedScore)
  const [humanVerdict, setHumanVerdict] = useState<'success' | 'failure'>(
    initialVerdict === 'failure' ? 'failure' : 'success',
  )
  const [rationale, setRationale] = useState(
    humanScore?.score_rationale ?? automatedScore?.score_rationale ?? '',
  )
  const [isUpdatingResult, setIsUpdatingResult] = useState(false)
  const [resultError, setResultError] = useState('')
  const [showAutomatedIdentity, setShowAutomatedIdentity] = useState(false)
  const contentRef = useRef<HTMLElement>(null)

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
      setDraft('')
    } catch {
      setError('Unable to save the objective.')
    } finally {
      setIsSaving(false)
    }
  }

  const handleUpdateResult = async (): Promise<void> => {
    if (!onUpdateHumanScore || !canUpdateOutcome) return

    setIsUpdatingResult(true)
    setResultError('')
    try {
      await onUpdateHumanScore(humanVerdict === 'success', rationale)
    } catch {
      setResultError('Unable to update the attack result.')
    } finally {
      setIsUpdatingResult(false)
    }
  }

  const handleRemoveResult = async (): Promise<void> => {
    if (!onRemoveHumanScore || !canRemoveHumanScore) return

    setIsUpdatingResult(true)
    setResultError('')
    try {
      await onRemoveHumanScore()
    } catch {
      setResultError('Unable to remove the human score.')
    } finally {
      setIsUpdatingResult(false)
    }
  }

  const resultControl = outcome && (
    <div className={styles.outcomeSection}>
      <Badge className={styles.label} appearance="tint" color="brand" size="small">
        Objective Achieved Outcome
      </Badge>
      <Popover
        withArrow
        positioning={{ autoSize: 'height' }}
        onOpenChange={(_event, data) => {
          if (data.open) {
            const currentHumanVerdict = scoreVerdict(humanScore ?? automatedScore)
            setHumanVerdict(currentHumanVerdict === 'failure' ? 'failure' : 'success')
            setRationale(humanScore?.score_rationale ?? automatedScore?.score_rationale ?? '')
            setResultError('')
            setShowAutomatedIdentity(false)
          }
        }}
      >
        <PopoverTrigger disableButtonEnhancement>
          <Button
            appearance="transparent"
            size="small"
            className={styles.outcomeButton}
            aria-label={`Objective achieved outcome: ${outcome}`}
            data-testid="objective-outcome-button"
          >
            <OutcomeBadge outcome={outcome} appearance="tint" size="small" />
          </Button>
        </PopoverTrigger>
        <PopoverSurface className={styles.resultPopover}>
          <Text size={400} weight="semibold">Attack Result Details</Text>
          <div className={styles.resultScoreRow}>
            <Text weight="semibold">Automated score</Text>
            {automatedScore ? (
              <Button
                appearance="subtle"
                size="small"
                icon={<InfoRegular />}
                className={styles.scoreValueButton}
                onClick={() => setShowAutomatedIdentity((visible) => !visible)}
                aria-expanded={showAutomatedIdentity}
                aria-label={`${scoreLabel(automatedScore)} automated score. ${
                  showAutomatedIdentity ? 'Hide' : 'View'
                } scorer details`}
              >
                {scoreLabel(automatedScore)} · {showAutomatedIdentity ? 'Hide details' : 'View details'}
              </Button>
            ) : (
              <Text className={styles.scoreValueText}>Not set</Text>
            )}
          </div>
          {showAutomatedIdentity && automatedScore && (
            <div className={styles.scorerIdentity} data-testid="automated-scorer-identity">
              <Text size={200}><strong>Scorer:</strong> {automatedScore.scorer_type}</Text>
              {automatedScore.scorer_class_identifier?.class_module && (
                <Text size={200}>
                  <strong>Module:</strong> {automatedScore.scorer_class_identifier.class_module}
                </Text>
              )}
              {automatedScore.score_rationale && (
                <Text size={200} className={styles.identityValue}>
                  <strong>Rationale:</strong> {automatedScore.score_rationale}
                </Text>
              )}
            </div>
          )}
          <div className={styles.resultScoreRow}>
            <Text weight="semibold">Human score</Text>
            <Text className={styles.scoreValueText}>{scoreLabel(humanScore)}</Text>
          </div>
          <Field label="Update human score">
            <RadioGroup
              className={styles.verdictOptions}
              layout="horizontal"
              value={humanVerdict}
              onChange={(_event, data) => setHumanVerdict(data.value as 'success' | 'failure')}
              disabled={!canUpdateOutcome}
            >
              <Radio value="success" label="Success" />
              <Radio value="failure" label="Failure" />
            </RadioGroup>
          </Field>
          <Field label="Rationale">
            <Textarea
              rows={5}
              value={rationale}
              onChange={(_event, data) => setRationale(data.value)}
              resize="vertical"
              disabled={!canUpdateOutcome}
            />
          </Field>
          {resultError && <Text role="alert">{resultError}</Text>}
          <div className={styles.resultActions}>
            {humanScore && onRemoveHumanScore && (
              <Button
                appearance="secondary"
                className={styles.resultAction}
                onClick={handleRemoveResult}
                disabled={!canRemoveHumanScore || isUpdatingResult}
              >
                Remove human score
              </Button>
            )}
            <Button
              appearance="primary"
              className={styles.resultAction}
              onClick={handleUpdateResult}
              disabled={!canUpdateOutcome || isUpdatingResult}
            >
              {isUpdatingResult ? 'Updating...' : 'Update'}
            </Button>
          </div>
        </PopoverSurface>
      </Popover>
    </div>
  )

  if (!objective) {
    const canShowObjective = (canAdd || isEditing) && Boolean(onAdd)
    if (!canShowObjective && !outcome) return null
    return (
      <div className={mergeClasses(styles.root, styles.emptyRoot)} data-testid="objective-header">
        {canShowObjective && (
          <div className={styles.headerSection}>
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
                <Button appearance="primary" size="small" className={styles.editorAction} onClick={handleSave} disabled={!draft.trim() || isSaving}>
                  {isSaving ? 'Saving...' : 'Save'}
                </Button>
                <Button appearance="subtle" size="small" className={styles.editorAction} onClick={() => setIsEditing(false)} disabled={isSaving}>
                  Cancel
                </Button>
                {error && <Text role="alert">{error}</Text>}
              </>
            ) : (
              <Button appearance="subtle" size="small" icon={<AddRegular />} onClick={() => setIsEditing(true)} className={styles.addButton}>
                Add objective
              </Button>
            )}
          </div>
        )}
        {resultControl}
      </div>
    )
  }

  const showToggle = overflowing || expanded

  return (
    <div className={styles.root} data-testid="objective-header">
      <div className={styles.headerSection}>
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
      {resultControl}
    </div>
  )
}
