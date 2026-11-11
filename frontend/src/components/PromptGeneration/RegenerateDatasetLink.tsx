import { useEffect, useState } from 'react'

import { Button, Text } from '@fluentui/react-components'
import { ArrowRepeatAllRegular } from '@fluentui/react-icons'
import { Link } from 'react-router'

import { promptGenerationsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import { isNamedDatasetSelectionKey } from '@/utils/routeParams'

import { newPromptGenerationRoutePath } from './promptGenerationRoutes'
import { usePromptGenerationStyles } from './PromptGeneration.styles'

interface RegenerateDatasetLinkProps {
  readonly selectionKey: string
}

interface DatasetSource {
  readonly selectionKey: string
  readonly attempt: number
  /** The generator that made the dataset, or null when no known generator did. */
  readonly generator: string | null
  readonly error: string | null
}

/** Links to a pre-filled generation form, shown only for datasets a known generator made. */
export default function RegenerateDatasetLink({ selectionKey }: RegenerateDatasetLinkProps) {
  const styles = usePromptGenerationStyles()
  const [attempt, setAttempt] = useState(0)
  const [source, setSource] = useState<DatasetSource | null>(null)
  const named = isNamedDatasetSelectionKey(selectionKey)

  useEffect(() => {
    if (!named) return
    const controller = new AbortController()
    const loadAsync = async (): Promise<void> => {
      try {
        const response = await promptGenerationsApi.getRequestFromDataset(selectionKey, controller.signal)
        if (controller.signal.aborted) return
        const generator = typeof response?.generator === 'string' ? response.generator : null
        setSource({ selectionKey, attempt, generator, error: null })
      } catch (error: unknown) {
        if (controller.signal.aborted) return
        const apiError = toApiError(error)
        // Only a 404 means no known generator made the dataset; other failures can be retried.
        setSource({ selectionKey, attempt, generator: null, error: apiError.status === 404 ? null : apiError.detail })
      }
    }
    void loadAsync()
    return () => { controller.abort() }
  }, [named, selectionKey, attempt])

  const current = source?.selectionKey === selectionKey && source.attempt === attempt ? source : null
  if (!named || current === null) return null
  if (current.error !== null) {
    return (
      <Text size={200} className={styles.muted} data-testid="regenerate-dataset-unavailable">
        Couldn't check whether this dataset can be regenerated: {current.error}{' '}
        <Button appearance="transparent" size="small" onClick={() => setAttempt((value) => value + 1)}>
          Check again
        </Button>
      </Text>
    )
  }
  if (current.generator === null) return null
  return (
    <Link
      className={styles.secondaryLink}
      to={newPromptGenerationRoutePath({ generator: current.generator, selectionKey })}
      data-testid="regenerate-dataset-link"
    >
      <ArrowRepeatAllRegular aria-hidden /> Edit policy &amp; regenerate
    </Link>
  )
}
