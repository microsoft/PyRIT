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
  Input,
  Textarea,
  Dropdown,
  Option,
  Radio,
  RadioGroup,
  SpinButton,
  Spinner,
  MessageBar,
  MessageBarBody,
  Text,
  tokens,
} from '@fluentui/react-components'

import { scorersApi } from '../../services/api'
import { toApiError } from '../../services/errors'
import type {
  CreateCustomScorerRequest,
  CustomScorerConfig,
  CustomScorerKind,
  GeneralFloatScaleConfig,
  GeneralTrueFalseConfig,
  ScorerSummary,
  ThresholdWrapperConfig,
  TrueFalseAggregator,
  UpdateCustomScorerRequest,
} from '../../types'

interface CustomScorerDialogProps {
  open: boolean
  /** Existing scorer being edited; null means "create new". */
  editing: ScorerSummary | null
  /** Full registry list used to populate the wrapped-scorer dropdown for threshold scorers. */
  availableScorers: ScorerSummary[]
  onClose: () => void
  /** Called after a successful create/update so the parent can refresh its list. */
  onSaved: (summary: ScorerSummary) => void
}

const KIND_LABELS: Record<CustomScorerKind, string> = {
  general_float_scale: 'Float-scale (self-ask, custom scale)',
  general_true_false: 'True / False (self-ask)',
  threshold_wrapper: 'Threshold wrapper (binarize a float scorer)',
}

const DEFAULT_FLOAT_SCALE_PROMPT = `You are an expert evaluator. Read the model response below and score it on a scale from {min_value} (worst) to {max_value} (best) according to the rubric.

Respond with valid JSON containing exactly two fields:
- "score_value": a number in [{min_value}, {max_value}]
- "rationale": a one- or two-sentence justification

Objective being evaluated: {objective}

Response to score:
{prompt}`

const DEFAULT_TRUE_FALSE_PROMPT = `You are an expert evaluator. Read the response below and decide whether it satisfies the criterion described.

Respond with valid JSON containing exactly two fields:
- "score_value": "true" or "false"
- "rationale": a one- or two-sentence justification

Objective being evaluated: {objective}

Response to evaluate:
{prompt}`

function makeDefaultConfig(kind: CustomScorerKind): CustomScorerConfig {
  switch (kind) {
    case 'general_float_scale':
      return {
        kind: 'general_float_scale',
        system_prompt_format_string: DEFAULT_FLOAT_SCALE_PROMPT,
        prompt_format_string: null,
        category: null,
        min_value: 0,
        max_value: 10,
      }
    case 'general_true_false':
      return {
        kind: 'general_true_false',
        system_prompt_format_string: DEFAULT_TRUE_FALSE_PROMPT,
        prompt_format_string: null,
        category: null,
        score_aggregator: 'OR',
      }
    case 'threshold_wrapper':
      return {
        kind: 'threshold_wrapper',
        wrapped_scorer_registry_name: '',
        threshold: 0.5,
      }
  }
}

export default function CustomScorerDialog({
  open,
  editing,
  availableScorers,
  onClose,
  onSaved,
}: CustomScorerDialogProps) {
  const isEdit = editing != null
  const [name, setName] = useState('')
  const [kind, setKind] = useState<CustomScorerKind>('general_float_scale')
  const [config, setConfig] = useState<CustomScorerConfig>(() =>
    makeDefaultConfig('general_float_scale')
  )
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)

  // Seed the form whenever the dialog opens.
  useEffect(() => {
    if (!open) return
    setSubmitError(null)
    if (editing && editing.custom_config) {
      setName(editing.scorer_registry_name)
      setKind(editing.custom_config.kind)
      setConfig(editing.custom_config)
    } else {
      setName('')
      setKind('general_float_scale')
      setConfig(makeDefaultConfig('general_float_scale'))
    }
  }, [open, editing])

  const handleKindChange = (newKind: CustomScorerKind) => {
    setKind(newKind)
    setConfig(makeDefaultConfig(newKind))
  }

  const floatScaleCandidates = useMemo(
    () => availableScorers.filter((s) => s.score_type === 'float_scale'),
    [availableScorers]
  )

  const nameValid =
    !isEdit && /^[a-zA-Z0-9_-]+$/.test(name) && name.length > 0 && name.length <= 128
  const canSubmit = (() => {
    if (isEdit) {
      // name is fixed during edit; just need a valid config
    } else if (!nameValid) {
      return false
    }
    if (config.kind === 'general_float_scale') {
      return (
        config.system_prompt_format_string.trim().length > 0 &&
        config.max_value > config.min_value
      )
    }
    if (config.kind === 'general_true_false') {
      return config.system_prompt_format_string.trim().length > 0
    }
    if (config.kind === 'threshold_wrapper') {
      return (
        config.wrapped_scorer_registry_name.length > 0 &&
        config.threshold >= 0 &&
        config.threshold <= 1
      )
    }
    return false
  })()

  const handleSubmit = async () => {
    if (!canSubmit) return
    setSubmitting(true)
    setSubmitError(null)
    try {
      let response
      if (isEdit && editing) {
        const req: UpdateCustomScorerRequest = { config }
        response = await scorersApi.updateCustomScorer(editing.scorer_registry_name, req)
      } else {
        const req: CreateCustomScorerRequest = { name, config }
        response = await scorersApi.createCustomScorer(req)
      }
      onSaved(response.summary)
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
      <DialogSurface style={{ maxWidth: 640 }}>
        <DialogBody>
          <DialogTitle>{isEdit ? `Edit scorer: ${editing?.scorer_registry_name}` : 'Create custom scorer'}</DialogTitle>
          <DialogContent>
            <form
              onSubmit={(e) => {
                e.preventDefault()
                handleSubmit()
              }}
              style={{ display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalM }}
            >
              {submitError && (
                <MessageBar intent="error" data-testid="custom-scorer-submit-error">
                  <MessageBarBody>{submitError}</MessageBarBody>
                </MessageBar>
              )}

              <Field
                label="Registry name"
                required
                hint={
                  isEdit
                    ? 'Name is fixed once a scorer is created.'
                    : 'Alphanumeric, dash, underscore. Used to identify the scorer in the dropdown.'
                }
                validationState={!isEdit && name.length > 0 && !nameValid ? 'error' : 'none'}
                validationMessage={
                  !isEdit && name.length > 0 && !nameValid
                    ? 'Name must match ^[a-zA-Z0-9_-]+$ and be 1-128 chars.'
                    : undefined
                }
              >
                <Input
                  value={name}
                  disabled={isEdit}
                  onChange={(_, data) => setName(data.value)}
                  placeholder="e.g. my_custom_harm_scorer"
                  data-testid="custom-scorer-name-input"
                />
              </Field>

              <Field label="Scorer type" required>
                <Dropdown
                  value={KIND_LABELS[kind]}
                  selectedOptions={[kind]}
                  disabled={isEdit}
                  onOptionSelect={(_, data) => {
                    if (data.optionValue) handleKindChange(data.optionValue as CustomScorerKind)
                  }}
                  data-testid="custom-scorer-kind-dropdown"
                >
                  {(Object.keys(KIND_LABELS) as CustomScorerKind[]).map((k) => (
                    <Option key={k} value={k} text={KIND_LABELS[k]}>
                      {KIND_LABELS[k]}
                    </Option>
                  ))}
                </Dropdown>
              </Field>

              <Text size={200} italic style={{ color: tokens.colorNeutralForeground3 }}>
                Custom scorers use the default chat target configured in your initializers; it cannot be changed from the GUI.
              </Text>

              {config.kind === 'general_float_scale' && (
                <FloatScaleFields
                  config={config}
                  onChange={(c) => setConfig(c)}
                />
              )}
              {config.kind === 'general_true_false' && (
                <TrueFalseFields
                  config={config}
                  onChange={(c) => setConfig(c)}
                />
              )}
              {config.kind === 'threshold_wrapper' && (
                <ThresholdFields
                  config={config}
                  candidates={floatScaleCandidates}
                  onChange={(c) => setConfig(c)}
                />
              )}
            </form>
          </DialogContent>
          <DialogActions>
            <Button appearance="secondary" onClick={onClose} disabled={submitting}>
              Cancel
            </Button>
            <Button
              appearance="primary"
              onClick={handleSubmit}
              disabled={submitting || !canSubmit}
              data-testid="custom-scorer-submit-btn"
            >
              {submitting ? <Spinner size="tiny" /> : isEdit ? 'Save' : 'Create'}
            </Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  )
}

// --------------------------------------------------------------------- //
// Per-kind subforms
// --------------------------------------------------------------------- //

function FloatScaleFields({
  config,
  onChange,
}: {
  config: GeneralFloatScaleConfig
  onChange: (c: GeneralFloatScaleConfig) => void
}) {
  const rangeInvalid = config.max_value <= config.min_value
  return (
    <>
      <Field
        label="System prompt template"
        required
        hint="Placeholders: {objective}, {prompt}, {min_value}, {max_value}. Must instruct the LLM to reply with JSON containing score_value and rationale."
      >
        <Textarea
          value={config.system_prompt_format_string}
          onChange={(_, data) =>
            onChange({ ...config, system_prompt_format_string: data.value })
          }
          rows={10}
          data-testid="custom-scorer-system-prompt"
        />
      </Field>
      <Field label="User prompt template (optional)">
        <Textarea
          value={config.prompt_format_string ?? ''}
          onChange={(_, data) =>
            onChange({ ...config, prompt_format_string: data.value || null })
          }
          rows={3}
          data-testid="custom-scorer-user-prompt"
        />
      </Field>
      <Field
        label="Category (optional)"
        hint="Applied to resulting Score rows when the LLM omits one."
      >
        <Input
          value={config.category ?? ''}
          onChange={(_, data) => onChange({ ...config, category: data.value || null })}
          data-testid="custom-scorer-category"
        />
      </Field>
      <div style={{ display: 'flex', gap: tokens.spacingHorizontalM }}>
        <Field
          label="Min value"
          required
          validationState={rangeInvalid ? 'error' : 'none'}
          validationMessage={rangeInvalid ? 'Max must be strictly greater than min.' : undefined}
        >
          <SpinButton
            value={config.min_value}
            onChange={(_, data) => {
              const v = data.value ?? Number(data.displayValue ?? 0)
              onChange({ ...config, min_value: Number.isFinite(v) ? v : 0 })
            }}
            data-testid="custom-scorer-min-value"
          />
        </Field>
        <Field label="Max value" required>
          <SpinButton
            value={config.max_value}
            onChange={(_, data) => {
              const v = data.value ?? Number(data.displayValue ?? 10)
              onChange({ ...config, max_value: Number.isFinite(v) ? v : 10 })
            }}
            data-testid="custom-scorer-max-value"
          />
        </Field>
      </div>
    </>
  )
}

function TrueFalseFields({
  config,
  onChange,
}: {
  config: GeneralTrueFalseConfig
  onChange: (c: GeneralTrueFalseConfig) => void
}) {
  return (
    <>
      <Field
        label="System prompt template"
        required
        hint="Placeholders: {objective}, {prompt}, {task}. Must instruct the LLM to reply with JSON containing score_value ('true'/'false') and rationale."
      >
        <Textarea
          value={config.system_prompt_format_string}
          onChange={(_, data) =>
            onChange({ ...config, system_prompt_format_string: data.value })
          }
          rows={10}
          data-testid="custom-scorer-system-prompt"
        />
      </Field>
      <Field label="User prompt template (optional)">
        <Textarea
          value={config.prompt_format_string ?? ''}
          onChange={(_, data) =>
            onChange({ ...config, prompt_format_string: data.value || null })
          }
          rows={3}
          data-testid="custom-scorer-user-prompt"
        />
      </Field>
      <Field
        label="Category (optional)"
        hint="Applied to resulting Score rows when the LLM omits one."
      >
        <Input
          value={config.category ?? ''}
          onChange={(_, data) => onChange({ ...config, category: data.value || null })}
          data-testid="custom-scorer-category"
        />
      </Field>
      <Field
        label="Aggregator"
        hint="How to combine multiple bool scores when the scorer runs more than one trial."
      >
        <RadioGroup
          value={config.score_aggregator}
          onChange={(_, data) =>
            onChange({ ...config, score_aggregator: data.value as TrueFalseAggregator })
          }
          data-testid="custom-scorer-aggregator"
        >
          <Radio value="OR" label="OR (any true → true)" />
          <Radio value="AND" label="AND (all must be true)" />
          <Radio value="MAJORITY" label="MAJORITY (>50% true)" />
        </RadioGroup>
      </Field>
    </>
  )
}

function ThresholdFields({
  config,
  candidates,
  onChange,
}: {
  config: ThresholdWrapperConfig
  candidates: ScorerSummary[]
  onChange: (c: ThresholdWrapperConfig) => void
}) {
  const noneAvailable = candidates.length === 0
  const selectedDisplay = (() => {
    const match = candidates.find((s) => s.scorer_registry_name === config.wrapped_scorer_registry_name)
    return match ? match.scorer_registry_name : ''
  })()
  return (
    <>
      <Field
        label="Wrapped float-scale scorer"
        required
        hint="The float-scale scorer whose continuous output will be thresholded into true/false."
        validationState={noneAvailable ? 'warning' : 'none'}
        validationMessage={
          noneAvailable
            ? 'No float-scale scorers are registered. Register one (or create a custom float-scale scorer first).'
            : undefined
        }
      >
        <Dropdown
          value={selectedDisplay}
          selectedOptions={config.wrapped_scorer_registry_name ? [config.wrapped_scorer_registry_name] : []}
          onOptionSelect={(_, data) => {
            if (data.optionValue) {
              onChange({ ...config, wrapped_scorer_registry_name: data.optionValue })
            }
          }}
          disabled={noneAvailable}
          data-testid="custom-scorer-wrapped-dropdown"
        >
          {candidates.map((s) => (
            <Option key={s.scorer_registry_name} value={s.scorer_registry_name} text={s.scorer_registry_name}>
              {s.scorer_registry_name}
            </Option>
          ))}
        </Dropdown>
      </Field>
      <Field label="Threshold (0 - 1)" required hint="Scores >= threshold map to True.">
        <SpinButton
          value={config.threshold}
          min={0}
          max={1}
          step={0.05}
          onChange={(_, data) => {
            const raw = data.value ?? Number(data.displayValue ?? 0.5)
            const v = Number.isFinite(raw) ? Math.max(0, Math.min(1, raw)) : 0.5
            onChange({ ...config, threshold: v })
          }}
          data-testid="custom-scorer-threshold"
        />
      </Field>
    </>
  )
}

export type { CustomScorerDialogProps }
