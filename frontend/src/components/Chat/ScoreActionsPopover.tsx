import { useEffect, useMemo, useState, type ReactNode } from 'react'
import {
  Button,
  Field,
  Input,
  Popover,
  PopoverSurface,
  PopoverTrigger,
  Spinner,
  Text,
  Textarea,
  tokens,
} from '@fluentui/react-components'

import { scoresApi } from '../../services/api'
import { toApiError } from '../../services/errors'
import type { BackendScore } from '../../types'

interface ScoreActionsPopoverProps {
  score: BackendScore
  onScoresChanged: () => void
  children: ReactNode
}

function validateScoreValue(score: BackendScore, value: string): string | null {
  const trimmed = value.trim()
  if (!trimmed) {
    return 'Score value is required.'
  }
  if (score.score_type === 'true_false') {
    return ['true', 'false'].includes(trimmed.toLowerCase())
      ? null
      : "Score value must be 'true' or 'false'."
  }
  if (score.score_type === 'float_scale') {
    const numeric = Number(trimmed)
    if (Number.isNaN(numeric)) {
      return 'Score value must be numeric.'
    }
    if (numeric < 0 || numeric > 1) {
      return 'Score value must be between 0.0 and 1.0.'
    }
  }
  return null
}

export default function ScoreActionsPopover({
  score,
  onScoresChanged,
  children,
}: ScoreActionsPopoverProps) {
  const [open, setOpen] = useState(false)
  const [isEditing, setIsEditing] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [scoreValue, setScoreValue] = useState(score.score_value)
  const [scoreRationale, setScoreRationale] = useState(score.score_rationale ?? '')

  useEffect(() => {
    if (!open) {
      setIsEditing(false)
      setError('')
    }
    setScoreValue(score.score_value)
    setScoreRationale(score.score_rationale ?? '')
  }, [open, score])

  const validationError = useMemo(
    () => validateScoreValue(score, scoreValue),
    [score, scoreValue]
  )

  const handleRerun = async () => {
    setIsSubmitting(true)
    setError('')
    try {
      await scoresApi.rerunScore(score.score_id)
      setOpen(false)
      onScoresChanged()
    } catch (err) {
      setError(toApiError(err).detail)
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleSave = async () => {
    if (validationError) {
      setError(validationError)
      return
    }

    setIsSubmitting(true)
    setError('')
    try {
      await scoresApi.editScore(score.score_id, {
        score_value: scoreValue.trim(),
        score_rationale: scoreRationale,
      })
      setOpen(false)
      onScoresChanged()
    } catch (err) {
      setError(toApiError(err).detail)
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <Popover open={open} onOpenChange={(_, data) => setOpen(data.open)} withArrow>
      <PopoverTrigger disableButtonEnhancement>
        <span style={{ display: 'inline-flex', cursor: 'pointer' }} tabIndex={0}>
          {children}
        </span>
      </PopoverTrigger>
      <PopoverSurface>
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: tokens.spacingVerticalS,
            minWidth: '320px',
            maxWidth: '420px',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: tokens.spacingHorizontalM }}>
            <Text weight="semibold">{score.scorer_type}</Text>
            <Text size={200}>{new Date(score.scored_at).toLocaleString()}</Text>
          </div>

          {!isEditing ? (
            <>
              <div style={{ display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalXS }}>
                <Text size={200}>Value: {score.score_value}</Text>
                <Text size={200}>Type: {score.score_type}</Text>
                <Text size={200}>
                  Rationale: {score.score_rationale?.trim() ? score.score_rationale : '—'}
                </Text>
              </div>
              {error && <Text style={{ color: tokens.colorPaletteRedForeground1 }}>{error}</Text>}
              <div style={{ display: 'flex', gap: tokens.spacingHorizontalS }}>
                <Button appearance="primary" onClick={handleRerun} disabled={isSubmitting}>
                  {isSubmitting ? <Spinner size="tiny" /> : 'Rerun'}
                </Button>
                <Button appearance="secondary" onClick={() => { setIsEditing(true); setError('') }} disabled={isSubmitting}>
                  Edit
                </Button>
              </div>
            </>
          ) : (
            <>
              <Field
                label="Score value"
                required
                validationState={validationError ? 'error' : 'none'}
                validationMessage={validationError || undefined}
              >
                <Input value={scoreValue} onChange={(_, data) => setScoreValue(data.value)} />
              </Field>
              <Field label="Rationale">
                <Textarea
                  value={scoreRationale}
                  onChange={(_, data) => setScoreRationale(data.value)}
                  rows={4}
                />
              </Field>
              {error && <Text style={{ color: tokens.colorPaletteRedForeground1 }}>{error}</Text>}
              <div style={{ display: 'flex', gap: tokens.spacingHorizontalS }}>
                <Button appearance="primary" onClick={handleSave} disabled={isSubmitting || Boolean(validationError)}>
                  {isSubmitting ? <Spinner size="tiny" /> : 'Save'}
                </Button>
                <Button appearance="secondary" onClick={() => { setIsEditing(false); setError('') }} disabled={isSubmitting}>
                  Cancel
                </Button>
              </div>
            </>
          )}
        </div>
      </PopoverSurface>
    </Popover>
  )
}
