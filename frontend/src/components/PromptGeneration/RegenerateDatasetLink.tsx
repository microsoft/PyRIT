import { useEffect, useState } from 'react'

import { ArrowRepeatAllRegular } from '@fluentui/react-icons'
import { Link } from 'react-router'

import { promptGenerationsApi } from '@/services/api'

import { newPromptGenerationRoutePath } from './promptGenerationRoutes'
import { usePromptGenerationStyles } from './PromptGeneration.styles'

const NAMED_DATASET_PREFIX = 'dataset:named:'

interface RegenerateDatasetLinkProps {
  readonly selectionKey: string
}

interface DatasetSource {
  readonly selectionKey: string
  readonly generator: string
}

/** Links to a pre-filled generation form, shown only for datasets a known generator made. */
export default function RegenerateDatasetLink({ selectionKey }: RegenerateDatasetLinkProps) {
  const styles = usePromptGenerationStyles()
  const [source, setSource] = useState<DatasetSource | null>(null)
  const named = selectionKey.startsWith(NAMED_DATASET_PREFIX)

  useEffect(() => {
    if (!named) return
    const controller = new AbortController()
    const loadAsync = async (): Promise<void> => {
      try {
        const response = await promptGenerationsApi.getRequestFromDataset(selectionKey, controller.signal)
        if (!controller.signal.aborted && typeof response?.generator === 'string') {
          setSource({ selectionKey, generator: response.generator })
        }
      } catch {
        // Datasets that no known generator made have no regenerate link.
      }
    }
    void loadAsync()
    return () => { controller.abort() }
  }, [named, selectionKey])

  if (!named || source?.selectionKey !== selectionKey) return null
  return (
    <Link
      className={styles.secondaryLink}
      to={newPromptGenerationRoutePath({ generator: source.generator, selectionKey })}
      data-testid="regenerate-dataset-link"
    >
      <ArrowRepeatAllRegular aria-hidden /> Edit policy &amp; regenerate
    </Link>
  )
}
