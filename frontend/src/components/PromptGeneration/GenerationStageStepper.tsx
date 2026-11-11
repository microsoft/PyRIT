import { Field, ProgressBar, Text, mergeClasses } from '@fluentui/react-components'
import { CheckmarkRegular } from '@fluentui/react-icons'

import type { PromptGenerationProgress, PromptGenerationStatus, PromptGeneratorDescriptor } from '@/types'

import { formatCount } from './promptGenerationPresentation'
import { usePromptGenerationStyles } from './PromptGeneration.styles'

interface GenerationStageStepperProps {
  readonly descriptor: PromptGeneratorDescriptor | undefined
  readonly status: PromptGenerationStatus
  readonly progress: PromptGenerationProgress
}

interface StageView {
  readonly name: string
  readonly label: string
}

function stagesFor(
  descriptor: PromptGeneratorDescriptor | undefined,
  progress: PromptGenerationProgress,
): StageView[] {
  if (descriptor) return descriptor.stages.map((stage) => ({ name: stage.name, label: stage.label }))
  const names = [...progress.completed_stages]
  if (progress.current_stage && !names.includes(progress.current_stage)) names.push(progress.current_stage)
  return names.map((name) => ({ name, label: name }))
}

function currentStageNote(status: PromptGenerationStatus): string {
  if (status === 'running' || status === 'saving') return 'in progress'
  if (status === 'awaiting_review') return 'next, after review'
  if (status === 'queued') return 'queued'
  return 'next'
}

/** The generator's stages in order, marked done or current, and generated item counts. */
export default function GenerationStageStepper({ descriptor, status, progress }: GenerationStageStepperProps) {
  const styles = usePromptGenerationStyles()
  const stages = stagesFor(descriptor, progress)
  const completed = new Set(progress.completed_stages)
  const showCurrent = status !== 'completed'
  const itemSummary = `${formatCount(progress.items_completed)} of ${formatCount(progress.items_total)} items generated`

  return (
    <div className={styles.progress} data-testid="generation-progress">
      <ol className={styles.stepper} aria-label="Generation stages">
        {stages.map((stage, index) => {
          const done = completed.has(stage.name) || status === 'completed'
          const current = showCurrent && !done && stage.name === progress.current_stage
          return (
            <li
              key={stage.name}
              className={mergeClasses(styles.step, done && styles.stepDone, current && styles.stepCurrent)}
              aria-current={current ? 'step' : undefined}
              data-testid={`generation-stage-${stage.name}`}
            >
              {done ? <CheckmarkRegular aria-hidden /> : <Text size={200} aria-hidden>{index + 1}</Text>}
              <Text size={200}>
                {stage.label}
                {done && <span className={styles.muted}> (done)</span>}
                {current && <span className={styles.muted}> ({currentStageNote(status)})</span>}
              </Text>
            </li>
          )
        })}
      </ol>
      {progress.items_total > 0 && (
        <Field label="Generated items" hint={itemSummary}>
          <ProgressBar
            value={Math.min(progress.items_completed, progress.items_total)}
            max={progress.items_total}
            thickness="large"
          />
        </Field>
      )}
    </div>
  )
}
