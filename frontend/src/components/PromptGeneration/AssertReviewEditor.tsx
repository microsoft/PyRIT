import { useEffect, useState } from 'react'

import {
  Badge,
  Button,
  Field,
  Input,
  MessageBar,
  MessageBarBody,
  MessageBarTitle,
  Spinner,
  Switch,
  Text,
  Textarea,
} from '@fluentui/react-components'
import { AddRegular, ArrowSyncRegular, DeleteRegular, PlayRegular, SaveRegular } from '@fluentui/react-icons'

import { promptGenerationsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type {
  AssertReviewPayload,
  PromptGenerationEstimate,
  PromptGenerationReview,
} from '@/types'

import {
  newCategoryDraft,
  newReviewLevelDraft,
  newTermDraft,
  parseAssertReviewPayload,
  reviewDraftErrors,
  reviewDraftFromPayload,
  reviewPayloadFromDraft,
  type AssertReviewDraft,
  type CategoryDraft,
  type ReviewDimensionDraft,
  type ReviewLevelDraft,
  type TermDraft,
} from './assertReview'
import GenerationEstimateCard from './GenerationEstimateCard'
import type { PromptGenerationReviewProps } from './generatorUi'
import { stageLabel } from './promptGenerationPresentation'
import { usePromptGenerationStyles } from './PromptGeneration.styles'

const MIN_LEVELS = 2

interface ReviewSnapshot {
  readonly key: string
  readonly review: PromptGenerationReview | null
  readonly error: string | null
}

/** Loads the paused ASSERT policy so it can be edited before seed groups are written. */
export default function AssertReviewEditor({ generation, descriptor, onGenerationChange }: PromptGenerationReviewProps) {
  const styles = usePromptGenerationStyles()
  const [reloadCount, setReloadCount] = useState(0)
  const [snapshot, setSnapshot] = useState<ReviewSnapshot | null>(null)
  const key = `${generation.id}:${reloadCount}`

  useEffect(() => {
    const controller = new AbortController()
    const loadAsync = async (): Promise<void> => {
      try {
        const review = await promptGenerationsApi.getReview(generation.id, controller.signal)
        if (!controller.signal.aborted) setSnapshot({ key, review, error: null })
      } catch (error: unknown) {
        if (!controller.signal.aborted) setSnapshot({ key, review: null, error: toApiError(error).detail })
      }
    }
    void loadAsync()
    return () => { controller.abort() }
  }, [generation.id, key])

  const current = snapshot?.key === key ? snapshot : null
  const payload = current?.review ? parseAssertReviewPayload(current.review.payload) : null

  return (
    <section className={styles.section} aria-labelledby="assert-review-title" data-testid="assert-review-editor">
      <div className={styles.headerIdentity}>
        <Text id="assert-review-title" as="h2" size={500} weight="semibold">Review the generated policy</Text>
        <Text className={styles.muted}>
          Generation paused after {stageLabel(descriptor, current?.review?.stage ?? 'dimensions').toLowerCase()}.
          Edit the taxonomy and dimension levels, then continue to write seed groups.
        </Text>
      </div>
      {current === null ? (
        <Spinner label="Loading review..." />
      ) : current.error || !current.review ? (
        <MessageBar intent="error">
          <MessageBarBody>
            The review could not be loaded: {current.error}
            <Button className={styles.touchTarget} icon={<ArrowSyncRegular />} onClick={() => setReloadCount((value) => value + 1)}>
              Retry
            </Button>
          </MessageBarBody>
        </MessageBar>
      ) : payload === null ? (
        <MessageBar intent="error">
          <MessageBarBody>The review has no taxonomy to edit. Continue generation or retry later.</MessageBarBody>
        </MessageBar>
      ) : (
        <AssertReviewForm
          key={key}
          generationId={generation.id}
          payload={payload}
          descriptor={descriptor}
          onGenerationChange={onGenerationChange}
        />
      )}
    </section>
  )
}

interface AssertReviewFormProps {
  readonly generationId: string
  readonly payload: AssertReviewPayload
  readonly descriptor: PromptGenerationReviewProps['descriptor']
  readonly onGenerationChange: PromptGenerationReviewProps['onGenerationChange']
}

function AssertReviewForm({
  generationId,
  payload,
  descriptor,
  onGenerationChange,
}: AssertReviewFormProps) {
  const styles = usePromptGenerationStyles()
  const systematization = payload.systematization
  const [draft, setDraft] = useState<AssertReviewDraft>(() => reviewDraftFromPayload(payload))
  const [saveCount, setSaveCount] = useState(0)
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState<'saving' | 'continuing' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [savedMessage, setSavedMessage] = useState<string | null>(null)
  const [estimate, setEstimate] = useState<{ saveCount: number; value: PromptGenerationEstimate | null } | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    const loadAsync = async (): Promise<void> => {
      try {
        const value = await promptGenerationsApi.getEstimate(generationId, controller.signal)
        if (!controller.signal.aborted) setEstimate({ saveCount, value })
      } catch {
        if (!controller.signal.aborted) setEstimate({ saveCount, value: null })
      }
    }
    void loadAsync()
    return () => { controller.abort() }
  }, [generationId, saveCount])

  const errors = reviewDraftErrors(draft)
  const permissibleCount = draft.categories.filter((category) => category.permissible).length

  const edit = (changes: Partial<AssertReviewDraft>): void => {
    setDraft((previous) => ({ ...previous, ...changes }))
    setDirty(true)
    setSavedMessage(null)
  }
  const editCategory = (id: string, changes: Partial<CategoryDraft>): void => {
    edit({ categories: draft.categories.map((category) => category.id === id ? { ...category, ...changes } : category) })
  }
  const editTerm = (id: string, changes: Partial<TermDraft>): void => {
    edit({ terms: draft.terms.map((term) => term.id === id ? { ...term, ...changes } : term) })
  }
  const editDimension = (id: string, changes: Partial<ReviewDimensionDraft>): void => {
    edit({ dimensions: draft.dimensions.map((dimension) => dimension.id === id ? { ...dimension, ...changes } : dimension) })
  }
  const editLevel = (dimension: ReviewDimensionDraft, levelId: string, changes: Partial<ReviewLevelDraft>): void => {
    editDimension(dimension.id, {
      levels: dimension.levels.map((level) => level.id === levelId ? { ...level, ...changes } : level),
    })
  }

  /** Saves edits; returns false when the save failed. */
  const saveAsync = async (): Promise<boolean> => {
    try {
      await promptGenerationsApi.saveReview(generationId, reviewPayloadFromDraft(draft))
      setSaveCount((value) => value + 1)
      setDirty(false)
      setSavedMessage('Changes saved.')
      return true
    } catch (cause: unknown) {
      setError(toApiError(cause).detail)
      return false
    }
  }

  const handleSave = async (): Promise<void> => {
    setBusy('saving')
    setError(null)
    await saveAsync()
    setBusy(null)
  }

  const handleContinue = async (): Promise<void> => {
    setBusy('continuing')
    setError(null)
    if (dirty && !(await saveAsync())) {
      setBusy(null)
      return
    }
    try {
      onGenerationChange(await promptGenerationsApi.continueAfterReview(generationId))
    } catch (cause: unknown) {
      setError(toApiError(cause).detail)
      setBusy(null)
    }
  }

  const disabled = busy !== null

  return (
    <div className={styles.form}>
      {systematization && (
        <div className={styles.section} data-testid="assert-review-systematization">
          <Text as="h3" size={400} weight="semibold">Research summary</Text>
          {systematization.scope && (
            <div className={styles.cellStack}>
              <Text weight="semibold">Scope</Text>
              <Text className={styles.preformatted}>{systematization.scope}</Text>
            </div>
          )}
          {systematization.reasoning_summary && (
            <div className={styles.cellStack}>
              <Text weight="semibold">Reasoning</Text>
              <Text className={styles.preformatted}>{systematization.reasoning_summary}</Text>
            </div>
          )}
        </div>
      )}

      <div className={styles.section}>
        <Text as="h3" size={400} weight="semibold">Behavior: {draft.behaviorName}</Text>
        <Field label="Behavior definition" required>
          <Textarea
            className={styles.wideControl}
            resize="vertical"
            rows={4}
            value={draft.behaviorDefinition}
            disabled={disabled}
            onChange={(_, data) => edit({ behaviorDefinition: data.value })}
          />
        </Field>
      </div>

      <div className={styles.section} data-testid="assert-review-categories">
        <div className={styles.sectionHeading}>
          <Text as="h3" size={400} weight="semibold">Behavior categories</Text>
          <Text size={200} className={styles.muted}>
            {draft.categories.length} categories: {permissibleCount} permissible, {draft.categories.length - permissibleCount} impermissible
          </Text>
        </div>
        {draft.categories.map((category, index) => (
          <div key={category.id} className={styles.repeatedItem} data-testid={`assert-review-category-${index}`}>
            <div className={styles.repeatedItemHeader}>
              <Badge appearance="tint" color={category.permissible ? 'success' : 'danger'}>
                {category.permissible ? 'Permissible' : 'Impermissible'}
              </Badge>
              <Button
                className={styles.touchTarget}
                appearance="subtle"
                icon={<DeleteRegular />}
                disabled={disabled}
                aria-label={`Remove category ${index + 1}`}
                onClick={() => edit({ categories: draft.categories.filter((item) => item.id !== category.id) })}
              />
            </div>
            <Field label={`Category ${index + 1} name`} required>
              <Input
                className={styles.control}
                value={category.name}
                disabled={disabled}
                onChange={(_, data) => editCategory(category.id, { name: data.value })}
              />
            </Field>
            <Switch
              checked={category.permissible}
              disabled={disabled}
              onChange={(_, data) => editCategory(category.id, { permissible: data.checked })}
              label={`Category ${index + 1} is permissible`}
            />
            <Field label={`Category ${index + 1} definition`} required>
              <Textarea
                className={styles.wideControl}
                resize="vertical"
                value={category.definition}
                disabled={disabled}
                onChange={(_, data) => editCategory(category.id, { definition: data.value })}
              />
            </Field>
            <Field label={`Category ${index + 1} examples`} required hint="One example per line.">
              <Textarea
                className={styles.wideControl}
                resize="vertical"
                value={category.examplesText}
                disabled={disabled}
                onChange={(_, data) => editCategory(category.id, { examplesText: data.value })}
              />
            </Field>
          </div>
        ))}
        <div className={styles.actions}>
          <Button
            className={styles.touchTarget}
            appearance="secondary"
            icon={<AddRegular />}
            disabled={disabled}
            onClick={() => edit({ categories: [...draft.categories, newCategoryDraft()] })}
            data-testid="assert-review-add-category"
          >
            Add category
          </Button>
        </div>
      </div>

      <div className={styles.section} data-testid="assert-review-terms">
        <Text as="h3" size={400} weight="semibold">Definitions of terms</Text>
        {draft.terms.length === 0 && <Text className={styles.muted}>No terms are defined.</Text>}
        {draft.terms.map((term, index) => (
          <div key={term.id} className={styles.repeatedItem}>
            <div className={styles.repeatedItemHeader}>
              <Text weight="semibold">{term.term.trim() || `Term ${index + 1}`}</Text>
              <Button
                className={styles.touchTarget}
                appearance="subtle"
                icon={<DeleteRegular />}
                disabled={disabled}
                aria-label={`Remove term ${index + 1}`}
                onClick={() => edit({ terms: draft.terms.filter((item) => item.id !== term.id) })}
              />
            </div>
            <Field label={`Term ${index + 1}`} required>
              <Input
                className={styles.control}
                value={term.term}
                disabled={disabled}
                onChange={(_, data) => editTerm(term.id, { term: data.value })}
              />
            </Field>
            <Field label={`Term ${index + 1} definition`} required>
              <Textarea
                className={styles.wideControl}
                resize="vertical"
                value={term.definition}
                disabled={disabled}
                onChange={(_, data) => editTerm(term.id, { definition: data.value })}
              />
            </Field>
            <Field label={`Term ${index + 1} examples`} hint="One example per line.">
              <Textarea
                className={styles.wideControl}
                resize="vertical"
                value={term.examplesText}
                disabled={disabled}
                onChange={(_, data) => editTerm(term.id, { examplesText: data.value })}
              />
            </Field>
          </div>
        ))}
        <div className={styles.actions}>
          <Button
            className={styles.touchTarget}
            appearance="secondary"
            icon={<AddRegular />}
            disabled={disabled}
            onClick={() => edit({ terms: [...draft.terms, newTermDraft()] })}
          >
            Add term
          </Button>
        </div>
      </div>

      {draft.dimensions.length > 0 && (
        <div className={styles.section} data-testid="assert-review-dimensions">
          <Text as="h3" size={400} weight="semibold">Coverage dimension levels</Text>
          {draft.dimensions.map((dimension) => (
            <div key={dimension.id} className={styles.repeatedItem}>
              <Text weight="semibold">{dimension.name}</Text>
              {dimension.description && <Text size={200} className={styles.muted}>{dimension.description}</Text>}
              {dimension.levels.map((level, levelIndex) => (
                <div key={level.id} className={styles.levelRow}>
                  <Input
                    aria-label={`${dimension.name} level ${levelIndex + 1} name`}
                    value={level.name}
                    disabled={disabled}
                    onChange={(_, data) => editLevel(dimension, level.id, { name: data.value })}
                  />
                  <Input
                    aria-label={`${dimension.name} level ${levelIndex + 1} definition`}
                    value={level.definition}
                    disabled={disabled}
                    onChange={(_, data) => editLevel(dimension, level.id, { definition: data.value })}
                  />
                  <Button
                    className={styles.touchTarget}
                    appearance="subtle"
                    icon={<DeleteRegular />}
                    disabled={disabled || dimension.levels.length <= MIN_LEVELS}
                    aria-label={`Remove ${dimension.name} level ${levelIndex + 1}`}
                    onClick={() => editDimension(dimension.id, {
                      levels: dimension.levels.filter((item) => item.id !== level.id),
                    })}
                  />
                </div>
              ))}
              <div className={styles.actions}>
                <Button
                  className={styles.touchTarget}
                  appearance="secondary"
                  icon={<AddRegular />}
                  disabled={disabled}
                  onClick={() => editDimension(dimension.id, { levels: [...dimension.levels, newReviewLevelDraft()] })}
                >
                  Add level to {dimension.name}
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      {errors.length > 0 && (
        <MessageBar intent="warning" data-testid="assert-review-errors">
          <MessageBarBody>
            <MessageBarTitle>Fix these before saving</MessageBarTitle>
            <ul className={styles.list}>
              {errors.map((message) => <li key={message}><Text size={200}>{message}</Text></li>)}
            </ul>
          </MessageBarBody>
        </MessageBar>
      )}
      {error && (
        <MessageBar intent="error">
          <MessageBarBody role="alert">{error}</MessageBarBody>
        </MessageBar>
      )}

      <GenerationEstimateCard
        title="Remaining work"
        estimate={estimate?.value ?? null}
        descriptor={descriptor}
        updating={estimate === null || estimate.saveCount !== saveCount}
        emptyMessage={estimate === null ? 'Loading the estimate...' : 'No estimate is available.'}
      />

      <div className={styles.actions}>
        <Button
          className={styles.touchTarget}
          appearance="primary"
          icon={<PlayRegular />}
          disabled={disabled || errors.length > 0}
          onClick={() => { void handleContinue() }}
          data-testid="assert-review-continue"
        >
          {busy === 'continuing' ? 'Continuing...' : dirty ? 'Save and continue generation' : 'Continue generation'}
        </Button>
        <Button
          className={styles.touchTarget}
          appearance="secondary"
          icon={<SaveRegular />}
          disabled={disabled || !dirty || errors.length > 0}
          onClick={() => { void handleSave() }}
          data-testid="assert-review-save"
        >
          {busy === 'saving' ? 'Saving...' : 'Save changes'}
        </Button>
        <Text role="status" size={200} className={styles.muted}>{dirty ? 'Unsaved changes' : savedMessage}</Text>
      </div>
    </div>
  )
}
