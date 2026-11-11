import { Button } from '@fluentui/react-components'
import { AddRegular, FolderOpenRegular } from '@fluentui/react-icons'
import { useLinkClickHandler } from 'react-router'

import { newPromptGenerationRoutePath } from '@/components/PromptGeneration/promptGenerationRoutes'
import { usePromptGenerators } from '@/hooks/usePromptGenerators'

import { useDatasetActionsStyles } from './DatasetActions.styles'

interface DatasetActionsProps {
  readonly scenarioName: string
}

/** Related catalog actions stay secondary to the scenario's dataset selection. */
export default function DatasetActions({ scenarioName }: DatasetActionsProps) {
  const styles = useDatasetActionsStyles()
  const { generators } = usePromptGenerators()
  const matching = generators.filter((generator) => generator.follow_up_scenario === scenarioName)
  const generator = matching.length === 1 ? matching[0] : undefined
  const destination = generator
    ? newPromptGenerationRoutePath({ generator: generator.name })
    : newPromptGenerationRoutePath()
  const browse = useLinkClickHandler<HTMLAnchorElement>('/datasets')
  const generate = useLinkClickHandler<HTMLAnchorElement>(destination)
  return <div className={styles.root} role="group" aria-label="Dataset actions">
    <Button as="a" href="/datasets" onClick={browse} role="link" appearance="secondary" icon={<FolderOpenRegular />}>
      Browse datasets
    </Button>
    <Button as="a" href={destination} onClick={generate} role="link" appearance="secondary" icon={<AddRegular />}>
      Generate datasets
    </Button>
  </div>
}
