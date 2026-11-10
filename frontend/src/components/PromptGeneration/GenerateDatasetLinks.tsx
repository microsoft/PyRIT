import { Link } from 'react-router'

import { usePromptGenerators } from '@/hooks/usePromptGenerators'

import { withIndefiniteArticle } from './promptGenerationPresentation'
import { newPromptGenerationRoutePath } from './promptGenerationRoutes'
import { usePromptGenerationStyles } from './PromptGeneration.styles'

interface GenerateDatasetLinksProps {
  /** Shows a link for each generator whose datasets this scenario runs. */
  readonly scenarioName: string
}

/** Unobtrusive links to generate a dataset for a scenario; renders nothing when no generator targets it. */
export default function GenerateDatasetLinks({ scenarioName }: GenerateDatasetLinksProps) {
  const styles = usePromptGenerationStyles()
  const { generators } = usePromptGenerators()
  const matching = generators.filter((generator) => generator.follow_up_scenario === scenarioName)
  if (matching.length === 0) return null
  return (
    <div className={styles.actions}>
      {matching.map((generator) => (
        <Link
          key={generator.name}
          className={styles.hintLink}
          to={newPromptGenerationRoutePath({ generator: generator.name })}
          data-testid={`generate-dataset-link-${generator.name}`}
        >
          Generate {withIndefiniteArticle(generator.display_name)}
        </Link>
      ))}
    </div>
  )
}
