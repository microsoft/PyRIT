import { useState } from 'react'

import { Field, Input, Text, Textarea } from '@fluentui/react-components'

import { usePromptGenerationPreflight } from '@/hooks/usePromptGenerationPreflight'
import { usePromptGenerationSubmit } from '@/hooks/usePromptGenerationSubmit'
import type { PromptGenerationCreateRequest, TargetObjectiveGenerationRequest } from '@/types'

import GenerationCommonFields from './GenerationCommonFields'
import GenerationFormFooter from './GenerationFormFooter'
import {
  initialTargetSelection,
  parseWholeNumber,
  preflightProblems,
  readNumberText,
  readString,
  readStringList,
  targetSelectionErrors,
} from './generationFormHelpers'
import type { PromptGenerationFormProps } from './generatorUi'
import { datasetNameError } from './promptGenerationPresentation'
import { usePromptGenerationStyles } from './PromptGeneration.styles'

const DEFAULT_OBJECTIVE_COUNT = 20
const MAX_OBJECTIVE_COUNT = 1_000
const MAX_HARM_CATEGORIES = 50
const MAX_HARM_CATEGORY_LENGTH = 100

function parseHarmCategories(text: string): string[] {
  return [...new Set(text.split(',').map((item) => item.trim()).filter(Boolean))]
}

/** Writes a list of distinct objectives from instructions. */
export default function TargetObjectiveGenerationForm({
  descriptor,
  targets,
  targetsLoading,
  targetsError,
  defaultTarget,
  seed,
}: PromptGenerationFormProps) {
  const styles = usePromptGenerationStyles()
  const [instructions, setInstructions] = useState(() => readString(seed.request?.instructions))
  const [count, setCount] = useState(() => readNumberText(seed.request?.count, DEFAULT_OBJECTIVE_COUNT))
  const [harmCategoriesText, setHarmCategoriesText] = useState(
    () => readStringList(seed.request?.harm_categories).join(', '),
  )
  const [datasetName, setDatasetName] = useState(seed.datasetName ?? '')
  const [selectedTargets, setSelectedTargets] = useState(
    () => initialTargetSelection(descriptor, seed.targets, defaultTarget),
  )
  const [pauseForReview, setPauseForReview] = useState(seed.pauseForReview ?? false)
  const { submitting, error: submitError, submit } = usePromptGenerationSubmit()

  const harmCategories = parseHarmCategories(harmCategoriesText)
  const objectiveCount = parseWholeNumber(count, 1, MAX_OBJECTIVE_COUNT)
  const nameError = datasetNameError(datasetName)
  const clientErrors: string[] = []
  if (!instructions.trim()) clientErrors.push('Enter instructions for the objectives.')
  if (objectiveCount === null) clientErrors.push(`Objectives must be a whole number from 1 to ${MAX_OBJECTIVE_COUNT.toLocaleString('en-US')}.`)
  if (harmCategories.length > MAX_HARM_CATEGORIES) clientErrors.push(`Use at most ${MAX_HARM_CATEGORIES} harm categories.`)
  if (harmCategories.some((category) => category.length > MAX_HARM_CATEGORY_LENGTH)) {
    clientErrors.push(`Each harm category must be at most ${MAX_HARM_CATEGORY_LENGTH} characters.`)
  }
  if (nameError) clientErrors.push(nameError)
  clientErrors.push(...targetSelectionErrors(descriptor.target_roles, targets, selectedTargets))

  const request: TargetObjectiveGenerationRequest | null = clientErrors.length === 0 && objectiveCount !== null
    ? { instructions, count: objectiveCount, harm_categories: harmCategories }
    : null
  const createRequest: PromptGenerationCreateRequest | null = request && {
    generator: descriptor.name,
    dataset_name: datasetName,
    request: { ...request },
    targets: selectedTargets,
    pause_for_review: pauseForReview,
  }
  const preflight = usePromptGenerationPreflight(createRequest ? [createRequest] : null)
  const problems = preflightProblems(preflight.results, [datasetName])

  const handleSubmit = (): void => {
    if (!request) return
    void submit({
      generator: descriptor.name,
      submission: { kind: 'single', datasetName, request: { ...request } },
      targets: selectedTargets,
      pauseForReview,
    })
  }

  return (
    <form
      className={styles.form}
      aria-label={`${descriptor.display_name} settings`}
      noValidate
      onSubmit={(event) => { event.preventDefault() }}
      data-testid="target-objective-generation-form"
    >
      <section className={styles.section} aria-labelledby="objective-request-title">
        <Text id="objective-request-title" as="h2" size={400} weight="semibold">Objectives</Text>
        <Field label="Instructions" required hint="Describe the objectives to write, such as the harm area and target system.">
          <Textarea
            className={styles.wideControl}
            resize="vertical"
            rows={6}
            value={instructions}
            disabled={submitting}
            onChange={(_, data) => setInstructions(data.value)}
            data-testid="objective-instructions"
          />
        </Field>
        <div className={styles.fieldGrid}>
          <Field label="Number of objectives" required>
            <Input
              className={styles.control}
              type="number"
              min={1}
              max={MAX_OBJECTIVE_COUNT}
              value={count}
              disabled={submitting}
              onChange={(_, data) => setCount(data.value)}
              data-testid="objective-count"
            />
          </Field>
          <Field label="Harm categories" hint="Comma-separated labels stored on every objective.">
            <Input
              className={styles.control}
              value={harmCategoriesText}
              disabled={submitting}
              onChange={(_, data) => setHarmCategoriesText(data.value)}
              data-testid="objective-harm-categories"
            />
          </Field>
        </div>
      </section>

      <GenerationCommonFields
        descriptor={descriptor}
        datasetName={{ value: datasetName, onChange: setDatasetName, error: datasetName ? nameError : null }}
        targets={targets}
        targetsLoading={targetsLoading}
        targetsError={targetsError}
        selectedTargets={selectedTargets}
        onTargetsChange={setSelectedTargets}
        pauseForReview={pauseForReview}
        onPauseForReviewChange={setPauseForReview}
        disabled={submitting}
      />

      <GenerationFormFooter
        descriptor={descriptor}
        clientErrors={clientErrors}
        preflight={preflight}
        problems={problems}
        datasetCount={1}
        submitting={submitting}
        submitError={submitError}
        onGenerate={handleSubmit}
      />
    </form>
  )
}
