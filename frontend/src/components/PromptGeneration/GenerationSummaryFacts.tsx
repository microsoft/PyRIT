import { usePromptGenerationStyles } from './PromptGeneration.styles'

export interface GenerationSummaryFact {
  readonly label: string
  readonly value: string
  /** A short note under the value, such as a breakdown. */
  readonly detail?: string
}

interface GenerationSummaryFactsProps {
  readonly facts: readonly GenerationSummaryFact[]
  readonly label: string
  readonly testId?: string
}

/** Labelled result figures, as a definition list. */
export default function GenerationSummaryFacts({ facts, label, testId }: GenerationSummaryFactsProps) {
  const styles = usePromptGenerationStyles()
  return (
    <dl className={styles.summaryGrid} aria-label={label} data-testid={testId}>
      {facts.map((fact) => (
        <div key={fact.label} className={styles.summaryItem}>
          <dt className={styles.metadataTerm}>{fact.label}</dt>
          <dd className={styles.summaryDefinition}>
            {fact.value}
            {fact.detail && <span className={styles.summaryDetail}>{fact.detail}</span>}
          </dd>
        </div>
      ))}
    </dl>
  )
}
