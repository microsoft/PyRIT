import { Checkbox, Field, Input, Text } from '@fluentui/react-components'

import type { PromptGeneratorDescriptor, TargetInstance } from '@/types'

import GenerationTargetFields from './GenerationTargetFields'
import { hasReviewableStage, type TargetRoleFilter } from './generationFormHelpers'
import { usePromptGenerationStyles } from './PromptGeneration.styles'

interface DatasetNameField {
  readonly value: string
  readonly onChange: (value: string) => void
  readonly error: string | null
}

interface GenerationCommonFieldsProps {
  readonly descriptor: PromptGeneratorDescriptor
  /** Omit when the generator form names its datasets itself, such as per behavior in a suite. */
  readonly datasetName?: DatasetNameField
  readonly targets: TargetInstance[]
  readonly targetsLoading: boolean
  readonly targetsError: string | null
  readonly selectedTargets: Readonly<Record<string, string>>
  readonly onTargetsChange: (value: Record<string, string>) => void
  readonly targetFilters?: Readonly<Record<string, TargetRoleFilter>>
  readonly pauseForReview: boolean
  readonly onPauseForReviewChange: (value: boolean) => void
  readonly disabled: boolean
}

/** The output dataset, targets, and review pause every generator form asks for. */
export default function GenerationCommonFields({
  descriptor,
  datasetName,
  targets,
  targetsLoading,
  targetsError,
  selectedTargets,
  onTargetsChange,
  targetFilters,
  pauseForReview,
  onPauseForReviewChange,
  disabled,
}: GenerationCommonFieldsProps) {
  const styles = usePromptGenerationStyles()
  const reviewableStages = descriptor.stages.filter((stage) => stage.reviewable).map((stage) => stage.label)

  return (
    <section className={styles.section} aria-labelledby="generation-output-title">
      <Text id="generation-output-title" as="h2" size={400} weight="semibold">Output and targets</Text>
      {datasetName && (
        <Field
          label="Dataset name"
          required
          hint="The name of the new stored dataset. It must not exist yet."
          validationMessage={datasetName.error ?? undefined}
          validationState={datasetName.error ? 'error' : 'none'}
        >
          <Input
            className={styles.control}
            value={datasetName.value}
            disabled={disabled}
            onChange={(_, data) => datasetName.onChange(data.value)}
            data-testid="generation-dataset-name"
          />
        </Field>
      )}
      <GenerationTargetFields
        roles={descriptor.target_roles}
        targets={targets}
        loading={targetsLoading}
        error={targetsError}
        value={selectedTargets}
        onChange={onTargetsChange}
        filters={targetFilters}
        disabled={disabled}
      />
      {hasReviewableStage(descriptor) && (
        <Checkbox
          checked={pauseForReview}
          disabled={disabled}
          onChange={(_, data) => onPauseForReviewChange(data.checked === true)}
          label={`Pause for review after: ${reviewableStages.join(', ')}`}
          data-testid="generation-pause-for-review"
        />
      )}
    </section>
  )
}
