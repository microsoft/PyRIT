import { Button, Tooltip } from '@fluentui/react-components'
import { AddRegular, FolderOpenRegular } from '@fluentui/react-icons'
import { useLinkClickHandler } from 'react-router'

import { newPromptGenerationRoutePath } from '@/components/PromptGeneration/promptGenerationRoutes'
import { usePromptGenerators } from '@/hooks/usePromptGenerators'
import { useRuntime } from '@/hooks/useRuntime'

import { useDatasetActionsStyles } from './DatasetActions.styles'

const CATALOG_PATH = '/datasets'

interface DatasetActionsProps {
  readonly scenarioName: string
}

/** Catalog actions beside a scenario's dataset selection; Generate opens the scenario's own generator when it has one. */
export default function DatasetActions({ scenarioName }: DatasetActionsProps) {
  const styles = useDatasetActionsStyles()
  const { ready } = useRuntime()
  const { generators, loading, refreshing } = usePromptGenerators()
  // A retained list can belong to a runtime that is reloading or unavailable, so wait for the current one.
  const resolved = ready && !loading && !refreshing
  const matching = generators.filter((generator) => generator.follow_up_scenario === scenarioName)
  const generator = matching.length === 1 ? matching[0] : undefined
  // Without a current list, including after a load error, the chooser shows what is available.
  const destination = newPromptGenerationRoutePath(generator ? { generator: generator.name } : {})
  const browse = useLinkClickHandler<HTMLAnchorElement>(CATALOG_PATH)
  const generate = useLinkClickHandler<HTMLAnchorElement>(destination)
  return (
    <div className={styles.root} role="group" aria-label="Dataset actions">
      <Button as="a" href={CATALOG_PATH} onClick={browse} role="link" appearance="secondary" icon={<FolderOpenRegular />}>
        Browse datasets
      </Button>
      {resolved ? (
        <Button as="a" href={destination} onClick={generate} role="link" appearance="secondary" icon={<AddRegular />}>
          Generate datasets
        </Button>
      ) : (
        <Tooltip content={ready ? 'Checking which generator this scenario uses.' : 'Available when PyRIT is ready.'}
          relationship="description">
          <Button className={styles.pending} appearance="secondary" icon={<AddRegular />} disabledFocusable>
            Generate datasets
          </Button>
        </Tooltip>
      )}
    </div>
  )
}
