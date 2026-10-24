import { useId, useLayoutEffect, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'

import {
  Badge,
  Button,
  Divider,
  Field,
  Input,
  MessageBar,
  MessageBarBody,
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
  LinkRegular,
} from '@fluentui/react-icons'

import OutcomeBadge from '@/components/OutcomeBadge'
import type { AttackOutcome, BackendScore, RegisteredComponentReference, ScoringFailure, ScorerInstance } from '@/types'
import { findRegisteredScorer } from '@/utils/scorerPresentation'
import { toApiError } from '@/services/errors'

import { useObjectiveHeaderStyles } from './ObjectiveHeader.styles'
import ChatScorerPicker from './ChatScorerPicker'

interface ObjectiveHeaderProps {
  objective: string
  draftMode?: boolean
  outcome?: AttackOutcome
  automatedScore?: BackendScore | null
  humanScore?: BackendScore | null
  canUpdateOutcome?: boolean
  canRemoveHumanScore?: boolean
  onUpdateHumanScore?: (value: boolean, rationale: string) => Promise<void>
  onRemoveHumanScore?: () => Promise<void>
  canAdd?: boolean
  onAdd?: (objective: string, expectedObjective: string) => Promise<void>
  /** Opens the finding dialog for the viewed conversation from Attack Result Details. */
  onAddFinding?: () => void
  canAddFinding?: boolean
  /** Receives the outcome badge button, so a dialog opened from its details can return focus to it. */
  outcomeButtonRef?: React.Ref<HTMLButtonElement>
  chatScorer?: RegisteredComponentReference | null
  chatScorerInherited?: boolean
  scoringFailure?: ScoringFailure | null
  scorers?: ScorerInstance[]
  scorersLoading?: boolean
  scorersError?: string | null
  canUpdateScorer?: boolean
  onUpdateScorer?: (reference: RegisteredComponentReference | null) => Promise<void>
}

function scoreVerdict(score?: BackendScore | null): 'success' | 'failure' | 'undetermined' {
  if (!score?.score_value) return 'undetermined'
  return score.score_value.toLowerCase() === 'true' ? 'success' : 'failure'
}

function scoreLabel(score?: BackendScore | null): string {
  const verdict = scoreVerdict(score)
  if (!score) return 'Not set'
  return verdict === 'undetermined' ? 'Undetermined' : verdict === 'success' ? 'Success' : 'Failure'
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
  onAddFinding,
  canAddFinding = false,
  outcomeButtonRef,
  chatScorer = null,
  chatScorerInherited = false,
  scoringFailure,
  scorers = [],
  scorersLoading,
  scorersError,
  canUpdateScorer = false,
  onUpdateScorer,
}: ObjectiveHeaderProps) {
  const styles = useObjectiveHeaderStyles()
  const [resultOpen, setResultOpen] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [overflowing, setOverflowing] = useState(false)
  const [isEditing, setIsEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [initialObjective, setInitialObjective] = useState('')
  const [isSaving, setIsSaving] = useState(false)
  const savePending = useRef(false)
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
  const [showAutomatedDetails, setShowAutomatedDetails] = useState(false)
  const [showScorerPicker, setShowScorerPicker] = useState(false)
  const contentRef = useRef<HTMLElement>(null)
  const humanScoreDisabledReasonId = useId()
  const missingObjective = !objective.trim()
  const ObjectiveContent = canAdd && onAdd ? 'button' : 'span'
  const registeredScorer = findRegisteredScorer(chatScorer, scorers, chatScorerInherited)
  const scorerName = registeredScorer?.scorer_registry_name ?? chatScorer?.registry_name ?? 'Not set'
  const scorerType = registeredScorer
    ? registeredScorer.display_type ?? registeredScorer.identifier.class_name
    : chatScorer ? scorersLoading ? 'Loading...' : 'Unavailable' : undefined
  const scorerLabel = scorerType ? `${scorerName} - ${scorerType}` : scorerName

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
  }, [objective, expanded, isEditing, ObjectiveContent])

  const beginEditing = (): void => {
    setDraft(objective)
    setInitialObjective(objective)
    setError('')
    setIsEditing(true)
  }

  const handleSave = async (): Promise<void> => {
    const trimmedObjective = draft.trim()
    if (savePending.current || (!trimmedObjective && missingObjective) || !onAdd) return

    savePending.current = true
    setIsSaving(true)
    setError('')
    try {
      await onAdd(trimmedObjective, initialObjective)
      setIsEditing(false)
      setDraft('')
    } catch {
      setError('Unable to save the objective.')
    } finally {
      savePending.current = false
      setIsSaving(false)
    }
  }

  const handleUpdateResult = async (): Promise<void> => {
    if (!onUpdateHumanScore || !canUpdateOutcome) return

    setIsUpdatingResult(true)
    setResultError('')
    try {
      await onUpdateHumanScore(humanVerdict === 'success', rationale)
      setResultOpen(false)
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

  const handleScorerChange = async (reference: RegisteredComponentReference | null): Promise<void> => {
    if (!canUpdateScorer || !onUpdateScorer) return
    setIsUpdatingResult(true)
    setResultError('')
    try {
      await onUpdateScorer(reference)
    } catch (cause: unknown) {
      setResultError(toApiError(cause).detail)
    } finally {
      setIsUpdatingResult(false)
    }
  }
  const scoringFailed = scoringFailure != null
  const resultControl = (outcome || objective.trim()) && (
    <div className={styles.outcomeSection}>
      <Badge className={styles.label} appearance="tint" color="brand" size="small">
        Objective Achieved Outcome
      </Badge>
      <Popover
        withArrow
        open={resultOpen}
        positioning={{ autoSize: 'height' }}
        onOpenChange={(_event, data) => {
          setResultOpen(data.open)
          if (data.open) {
            const currentHumanVerdict = scoreVerdict(humanScore ?? automatedScore)
            setHumanVerdict(currentHumanVerdict === 'failure' ? 'failure' : 'success')
            setRationale(humanScore?.score_rationale ?? automatedScore?.score_rationale ?? '')
            setResultError('')
            setShowAutomatedDetails(false)
            setShowScorerPicker(false)
          }
        }}
      >
        <PopoverTrigger disableButtonEnhancement>
          <Button
            ref={outcomeButtonRef}
            appearance="transparent"
            size="small"
            className={styles.outcomeButton}
            aria-label={`Objective achieved outcome: ${outcome ?? 'undetermined'}${scoringFailed ? '. Scoring error' : ''}`}
            data-testid="objective-outcome-button"
          >
            <OutcomeBadge outcome={outcome ?? 'undetermined'} appearance="tint" size="small" />
          </Button>
        </PopoverTrigger>
        <PopoverSurface className={styles.resultPopover}>
          <Text size={400} weight="semibold">Attack Result Details</Text>
          <div className={styles.resultScoreRow} role="group" aria-label="Automated scorer">
            <Text weight="semibold">Automated scorer</Text>
            {!missingObjective && onUpdateScorer ? (
              <Button appearance="subtle" size="small"
                className={mergeClasses(styles.scoreValueButton, styles.scorerValue)}
                icon={showScorerPicker ? <ChevronUpRegular /> : <ChevronDownRegular />} iconPosition="after"
                aria-label={`Automated scorer: ${scorerLabel}`} aria-expanded={showScorerPicker}
                onClick={() => setShowScorerPicker((visible) => !visible)}>
                <span className={styles.scorerLabel}>
                  <Text>{scorerName}</Text>
                  {scorerType && <Text size={200} className={styles.secondaryText}>{scorerType}</Text>}
                </span>
              </Button>
            ) : (
              <span className={mergeClasses(styles.scorerValue, styles.scorerLabel)}>
                <Text>{scorerName}</Text>
                {scorerType && <Text size={200} className={styles.secondaryText}>{scorerType}</Text>}
              </span>
            )}
          </div>
          {showScorerPicker && !missingObjective && onUpdateScorer && (
            <div className={styles.scorerSelection} role="region" aria-label="Automated scorer selection">
              <ChatScorerPicker value={chatScorer} scorers={scorers} inherited={chatScorerInherited}
                loading={scorersLoading} error={scorersError}
                disabled={!canUpdateScorer || isUpdatingResult}
                onChange={(reference: RegisteredComponentReference | null) => { void handleScorerChange(reference) }} />
              <Text size={200}>Scores future responses in all conversations in this attack.</Text>
            </div>
          )}
          <div className={styles.resultScoreRow} role="group" aria-label="Automated score">
            <Text weight="semibold">Automated score</Text>
            {scoringFailed && !missingObjective ? (
              <Button appearance="subtle" size="small" onClick={() => setShowAutomatedDetails((visible) => !visible)}
                aria-expanded={showAutomatedDetails}>Scoring error · {showAutomatedDetails ? 'Hide details' : 'View details'}</Button>
            ) : automatedScore ? (
              <Button
                appearance="subtle"
                size="small"
                icon={<InfoRegular />}
                className={styles.scoreValueButton}
                onClick={() => setShowAutomatedDetails((visible) => !visible)}
                aria-expanded={showAutomatedDetails}
                aria-label={`${scoreLabel(automatedScore)} automated score. ${
                  showAutomatedDetails ? 'Hide' : 'View'
                } score details`}
              >
                {scoreLabel(automatedScore)} · {showAutomatedDetails ? 'Hide details' : 'View details'}
              </Button>
            ) : (
              <Text className={styles.scoreValueText}>
                {chatScorer && !missingObjective ? 'This will be generated on the next turn' : 'Not set'}
              </Text>
            )}
          </div>
          {humanScore && automatedScore && (
            <Text size={200} className={styles.secondaryText}>Overridden by the human score.</Text>
          )}
          {showAutomatedDetails && automatedScore && (
            <div className={styles.scorerIdentity} data-testid="automated-score-details">
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
          {showAutomatedDetails && scoringFailed && (
            <MessageBar intent="error">
              <MessageBarBody>
                <pre className={styles.errorDetails} tabIndex={0} aria-label="Scorer failure details">
                  {`Scorer ${scoringFailure.scorer.registry_name}: ${scoringFailure.error}`}
                </pre>
              </MessageBarBody>
            </MessageBar>
          )}
          <Divider className={styles.resultDivider} aria-label="Human scoring" />
          <div className={styles.resultScoreRow}>
            <Text weight="semibold">Human score</Text>
            <Text className={styles.scoreValueText}>{scoreLabel(humanScore)}</Text>
          </div>
          {missingObjective && (
            <MessageBar intent="warning">
              <MessageBarBody id={humanScoreDisabledReasonId}>
                Add an objective to enable human scoring.
              </MessageBarBody>
            </MessageBar>
          )}
          <RadioGroup
            className={styles.verdictOptions}
            aria-label="Human score"
            layout="horizontal"
            value={humanVerdict}
            onChange={(_event, data) => setHumanVerdict(data.value as 'success' | 'failure')}
            disabled={!canUpdateOutcome}
            aria-describedby={missingObjective ? humanScoreDisabledReasonId : undefined}
          >
            <Radio value="success" label="Success" />
            <Radio value="failure" label="Failure" />
          </RadioGroup>
          <Field label="Rationale">
            <Textarea
              rows={5}
              value={rationale}
              onChange={(_event, data) => setRationale(data.value)}
              resize="vertical"
              disabled={!canUpdateOutcome}
              aria-describedby={missingObjective ? humanScoreDisabledReasonId : undefined}
            />
          </Field>
          {resultError && <Text role="alert">{resultError}</Text>}
          <div className={styles.resultActions}>
            {onAddFinding && (
              <Button
                appearance="secondary"
                icon={<LinkRegular />}
                className={styles.resultAction}
                disabled={!canAddFinding}
                onClick={() => { setResultOpen(false); onAddFinding() }}
              >
                Add as finding
              </Button>
            )}
            {humanScore && onRemoveHumanScore && (
              <Button
                appearance="secondary"
                className={styles.resultAction}
                onClick={handleRemoveResult}
                disabled={!canRemoveHumanScore || isUpdatingResult}
              >
                Remove
              </Button>
            )}
            <Button
              appearance="primary"
              className={styles.resultAction}
              onClick={handleUpdateResult}
              disabled={!canUpdateOutcome || isUpdatingResult}
              aria-describedby={missingObjective ? humanScoreDisabledReasonId : undefined}
            >
              {isUpdatingResult ? 'Saving...' : 'Add'}
            </Button>
          </div>
        </PopoverSurface>
      </Popover>
    </div>
  )

  if (!objective || isEditing) {
    const canShowObjective = (canAdd || isEditing) && Boolean(onAdd)
    if (!canShowObjective && !outcome) return null
    return (
      <div className={mergeClasses(styles.root, styles.emptyRoot)} data-testid="objective-header" data-conversation-editor-controls>
        {canShowObjective && (
          <div className={mergeClasses(styles.headerSection, styles.emptyHeaderSection)}>
            <Badge className={styles.label} appearance="tint" color="brand" size="small">
              Objective
            </Badge>
            {isEditing ? (
              <>
                <Input
                  className={styles.input}
                  value={draft}
                  onChange={(_event, data) => setDraft(data.value)}
                  onKeyDown={(event: KeyboardEvent<HTMLInputElement>) => {
                    if (event.key !== 'Enter' || event.nativeEvent.isComposing) return
                    event.preventDefault()
                    event.stopPropagation()
                    void handleSave()
                  }}
                  disabled={isSaving}
                  placeholder="Enter an objective"
                  aria-label="Attack objective"
                  autoFocus
                />
                <Button appearance="primary" size="small" className={styles.editorAction} onClick={handleSave} disabled={(!draft.trim() && missingObjective) || isSaving}>
                  {isSaving ? 'Saving...' : 'Save'}
                </Button>
                <Button appearance="subtle" size="small" className={styles.editorAction} onClick={() => setIsEditing(false)} disabled={isSaving}>
                  Cancel
                </Button>
                {error && <Text role="alert">{error}</Text>}
              </>
            ) : (
              <Button appearance="subtle" size="small" icon={<AddRegular />} onClick={beginEditing} className={styles.addButton}>
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
    <div className={styles.root} data-testid="objective-header" data-conversation-editor-controls>
      <div className={styles.headerSection}>
        <Badge className={styles.label} appearance="tint" color="brand" size="small">
          Objective
        </Badge>
        <ObjectiveContent
          type={canAdd && onAdd ? 'button' : undefined}
          aria-label={canAdd && onAdd ? 'Edit objective' : undefined}
          onClick={canAdd && onAdd ? beginEditing : undefined}
          ref={(element: HTMLButtonElement | HTMLSpanElement | null) => { contentRef.current = element }}
          className={mergeClasses(styles.content, canAdd && onAdd && styles.editableContent, expanded ? styles.contentExpanded : styles.contentCollapsed)}
          data-testid="objective-header-content"
        >
          {objective}
        </ObjectiveContent>
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
