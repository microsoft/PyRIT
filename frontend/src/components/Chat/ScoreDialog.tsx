import { useEffect, useMemo, useState } from 'react'
import {
  Dialog,
  DialogSurface,
  DialogBody,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Field,
  Combobox,
  Option,
  OptionGroup,
  Radio,
  RadioGroup,
  Input,
  Spinner,
  Badge,
  Text,
  Tooltip,
  MessageBar,
  MessageBarBody,
  tokens,
} from '@fluentui/react-components'
import { InfoRegular } from '@fluentui/react-icons'

import { attacksApi, scorersApi } from '../../services/api'
import { toApiError } from '../../services/errors'
import type {
  BackendScore,
  ScoreConversationMode,
  ScorerSummary,
} from '../../types'

type ScoreTarget =
  | { kind: 'conversation'; attackResultId: string; conversationId: string }
  | { kind: 'piece'; attackResultId: string; conversationId: string; pieceId: string }

interface ScoreDialogProps {
  open: boolean
  target: ScoreTarget | null
  onClose: () => void
  /** Called after a successful score so the caller can refetch messages/conversations. */
  onScored: (scores: BackendScore[]) => void
}

const MODE_LABELS: Record<ScoreConversationMode, string> = {
  last_message: 'Score last assistant message only',
  whole_conversation: 'Score the whole conversation (wraps in ConversationScorer)',
}

const SCORE_TYPE_LABELS: Record<string, string> = {
  true_false: 'True / False scorers',
  float_scale: 'Float-scale scorers',
  unknown: 'Other scorers',
}

const SCORE_TYPE_ORDER = ['true_false', 'float_scale', 'unknown']

/** Group scorers by their `score_type` so the dropdown can render <OptionGroup>s. */
function groupScorers(scorers: ScorerSummary[]): { score_type: string; items: ScorerSummary[] }[] {
  const groups = new Map<string, ScorerSummary[]>()
  for (const s of scorers) {
    const key = s.score_type || 'unknown'
    const existing = groups.get(key)
    if (existing) {
      existing.push(s)
    } else {
      groups.set(key, [s])
    }
  }
  const ordered: { score_type: string; items: ScorerSummary[] }[] = []
  for (const key of SCORE_TYPE_ORDER) {
    const items = groups.get(key)
    if (items) ordered.push({ score_type: key, items })
  }
  // Append any unexpected score_types we don't have a label for, alphabetically.
  for (const [key, items] of Array.from(groups.entries()).sort(([a], [b]) => a.localeCompare(b))) {
    if (!SCORE_TYPE_ORDER.includes(key)) {
      ordered.push({ score_type: key, items })
    }
  }
  return ordered
}

export default function ScoreDialog({ open, target, onClose, onScored }: ScoreDialogProps) {
  const [scorers, setScorers] = useState<ScorerSummary[]>([])
  const [loadingScorers, setLoadingScorers] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [selectedScorerName, setSelectedScorerName] = useState<string>('')
  const [scorerQuery, setScorerQuery] = useState<string>('')
  const [mode, setMode] = useState<ScoreConversationMode>('last_message')
  const [objective, setObjective] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)

  const isConversationScope = target?.kind === 'conversation'

  // Reset form whenever the dialog re-opens against a new target.
  useEffect(() => {
    if (!open) return
    setSelectedScorerName('')
    setScorerQuery('')
    setMode('last_message')
    setObjective('')
    setSubmitError(null)
  }, [open, target])

  // Fetch scorers when the dialog opens; cheap enough to refetch each time so
  // newly-registered scorers show up without a manual refresh.
  useEffect(() => {
    if (!open) return
    let cancelled = false
    setLoadingScorers(true)
    setLoadError(null)
    scorersApi
      .listScorers()
      .then((response) => {
        if (cancelled) return
        setScorers(response.items)
        if (response.items.length > 0) {
          const first = response.items[0]
          setSelectedScorerName((current) =>
            current && response.items.some((s) => s.scorer_registry_name === current)
              ? current
              : first.scorer_registry_name
          )
          setScorerQuery((current) => current || first.scorer_registry_name)
        }
      })
      .catch((err) => {
        if (cancelled) return
        setLoadError(toApiError(err).detail)
      })
      .finally(() => {
        if (!cancelled) setLoadingScorers(false)
      })
    return () => {
      cancelled = true
    }
  }, [open])

  const groupedScorers = useMemo(() => groupScorers(scorers), [scorers])
  const selectedScorer = useMemo(
    () => scorers.find((s) => s.scorer_registry_name === selectedScorerName) ?? null,
    [scorers, selectedScorerName]
  )

  // Whole-conversation scoring requires a FloatScale or TrueFalse scorer (the
  // ConversationScorer wrapper rejects others). Disable the mode and explain why
  // rather than letting the request fail server-side.
  const wholeConversationDisabled =
    selectedScorer != null && selectedScorer.score_type !== 'true_false' && selectedScorer.score_type !== 'float_scale'

  // If the user previously picked whole_conversation and then switched to an
  // unsupported scorer, snap the mode back to last_message so they can't submit
  // a request the backend will reject.
  useEffect(() => {
    if (wholeConversationDisabled && mode === 'whole_conversation') {
      setMode('last_message')
    }
  }, [wholeConversationDisabled, mode])

  const handleSubmit = async () => {
    if (!target || !selectedScorerName) return
    setSubmitting(true)
    setSubmitError(null)
    try {
      const trimmedObjective = objective.trim() || undefined
      if (target.kind === 'conversation') {
        const response = await attacksApi.scoreConversation(
          target.attackResultId,
          target.conversationId,
          {
            scorer_registry_name: selectedScorerName,
            mode,
            objective: trimmedObjective,
          }
        )
        onScored(response.scores)
      } else {
        const response = await attacksApi.scoreMessagePiece(
          target.attackResultId,
          target.conversationId,
          target.pieceId,
          {
            scorer_registry_name: selectedScorerName,
            objective: trimmedObjective,
          }
        )
        onScored(response.scores)
      }
      onClose()
    } catch (err) {
      setSubmitError(toApiError(err).detail)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(_, data) => {
        if (!data.open && !submitting) onClose()
      }}
    >
      <DialogSurface style={{ maxWidth: 560 }}>
        <DialogBody>
          <DialogTitle>
            {isConversationScope ? 'Score conversation' : 'Score message'}
          </DialogTitle>
          <DialogContent>
            <form
              onSubmit={(e) => {
                e.preventDefault()
                handleSubmit()
              }}
              style={{ display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalM }}
            >
              {loadError && (
                <MessageBar intent="error" data-testid="score-dialog-load-error">
                  <MessageBarBody>{loadError}</MessageBarBody>
                </MessageBar>
              )}
              {submitError && (
                <MessageBar intent="error" data-testid="score-dialog-submit-error">
                  <MessageBarBody>{submitError}</MessageBarBody>
                </MessageBar>
              )}

              {loadingScorers && (
                <div style={{ display: 'flex', justifyContent: 'center' }}>
                  <Spinner size="tiny" label="Loading scorers..." />
                </div>
              )}

              {!loadingScorers && !loadError && scorers.length === 0 && (
                <Text size={200} italic data-testid="score-dialog-empty">
                  No scorers are registered. Add one via your <code>~/.pyrit/.pyrit_conf</code> initializers.
                </Text>
              )}

              {!loadingScorers && scorers.length > 0 && (
                <Field
                  label={
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: tokens.spacingHorizontalXS }}>
                      Scorer
                      <Tooltip
                        content="Grouped by score type. Type to filter. Each entry is a pre-configured instance from the ScorerRegistry."
                        relationship="description"
                      >
                        <span style={{ display: 'inline-flex', color: tokens.colorNeutralForeground3 }}>
                          <InfoRegular fontSize={14} />
                        </span>
                      </Tooltip>
                    </span>
                  }
                  required
                >
                  <Combobox
                    value={scorerQuery}
                    selectedOptions={selectedScorerName ? [selectedScorerName] : []}
                    onOptionSelect={(_, data) => {
                      setSelectedScorerName(data.optionValue ?? '')
                      setScorerQuery(data.optionText ?? '')
                    }}
                    onChange={(e) => setScorerQuery((e.target as HTMLInputElement).value)}
                    placeholder="Search scorers..."
                    data-testid="score-dialog-scorer-select"
                  >
                    {groupedScorers.map((group) => (
                      <OptionGroup
                        key={group.score_type}
                        label={SCORE_TYPE_LABELS[group.score_type] ?? group.score_type}
                      >
                        {group.items.map((s) => (
                          <Option
                            key={s.scorer_registry_name}
                            value={s.scorer_registry_name}
                            text={s.scorer_registry_name}
                            data-testid={`scorer-option-${s.scorer_registry_name}`}
                          >
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                              <span>{s.scorer_registry_name}</span>
                              <Text size={100} style={{ color: tokens.colorNeutralForeground3 }}>
                                {s.scorer_type}
                              </Text>
                            </div>
                          </Option>
                        ))}
                      </OptionGroup>
                    ))}
                  </Combobox>
                </Field>
              )}

              {selectedScorer && (
                <div
                  data-testid="score-dialog-scorer-info"
                  style={{
                    border: `1px solid ${tokens.colorNeutralStroke2}`,
                    borderRadius: tokens.borderRadiusMedium,
                    padding: tokens.spacingVerticalS,
                    backgroundColor: tokens.colorNeutralBackground2,
                    display: 'flex',
                    flexDirection: 'column',
                    gap: tokens.spacingVerticalXS,
                  }}
                >
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: tokens.spacingHorizontalXS, alignItems: 'center' }}>
                    <Text size={200} weight="semibold">{selectedScorer.scorer_type}</Text>
                    <Badge appearance="outline" size="small">{selectedScorer.score_type}</Badge>
                    {(selectedScorer.tags ?? []).map((t) => (
                      <Badge key={t} appearance="tint" size="small" data-testid={`scorer-tag-${t}`}>{t}</Badge>
                    ))}
                  </div>
                  {selectedScorer.description ? (
                    <Text size={200} data-testid="score-dialog-scorer-description">
                      {selectedScorer.description}
                    </Text>
                  ) : (
                    <Text size={200} italic style={{ color: tokens.colorNeutralForeground3 }}>
                      No description available for this scorer.
                    </Text>
                  )}
                </div>
              )}

              {isConversationScope && (
                <Field label="Scope">
                  <RadioGroup
                    value={mode}
                    onChange={(_, data) => setMode(data.value as ScoreConversationMode)}
                    data-testid="score-dialog-mode-radio"
                  >
                    <Radio value="last_message" label={MODE_LABELS.last_message} />
                    <Radio
                      value="whole_conversation"
                      label={MODE_LABELS.whole_conversation}
                      disabled={wholeConversationDisabled}
                    />
                  </RadioGroup>
                  {wholeConversationDisabled && (
                    <Text size={200} italic>
                      Whole-conversation scoring requires a true/false or float-scale scorer.
                    </Text>
                  )}
                </Field>
              )}

              <Field
                label={
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: tokens.spacingHorizontalXS }}>
                    Objective (optional)
                    <Tooltip
                      content="Passed to the scorer as 'objective'. Used by self-ask scorers (refusal, task-achieved, scale) to judge whether the response satisfies this goal."
                      relationship="description"
                    >
                      <span style={{ display: 'inline-flex', color: tokens.colorNeutralForeground3 }}>
                        <InfoRegular fontSize={14} />
                      </span>
                    </Tooltip>
                  </span>
                }
              >
                <Input
                  value={objective}
                  onChange={(_, data) => setObjective(data.value)}
                  placeholder="e.g. The model agreed to draft a phishing email"
                  data-testid="score-dialog-objective-input"
                />
              </Field>
            </form>
          </DialogContent>
          <DialogActions>
            <Button appearance="secondary" onClick={onClose} disabled={submitting}>
              Cancel
            </Button>
            <Button
              appearance="primary"
              onClick={handleSubmit}
              disabled={submitting || loadingScorers || scorers.length === 0 || !selectedScorerName}
              data-testid="score-dialog-submit-btn"
            >
              {submitting ? <Spinner size="tiny" /> : 'Score'}
            </Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  )
}

export type { ScoreTarget, ScoreDialogProps }
