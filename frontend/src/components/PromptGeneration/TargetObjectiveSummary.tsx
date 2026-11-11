import GenerationSummaryFacts from './GenerationSummaryFacts'
import type { PromptGenerationSummaryProps } from './generatorUi'
import { formatCount } from './promptGenerationPresentation'

/** How many objectives a completed objective generation stored. */
export default function TargetObjectiveSummary({ summary }: PromptGenerationSummaryProps) {
  const count = typeof summary.objective_count === 'number' ? summary.objective_count : 0
  return (
    <GenerationSummaryFacts
      label="Generation results"
      testId="target-objective-summary"
      facts={[{ label: 'Objectives', value: formatCount(count) }]}
    />
  )
}
