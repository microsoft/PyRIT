import { Button, Text } from '@fluentui/react-components'
import { ArrowRepeatAllRegular } from '@fluentui/react-icons'
import { Link } from 'react-router'

import { useGenerationRequestFromDataset } from '@/hooks/useGenerationRequestFromDataset'
import { isNamedDatasetSelectionKey } from '@/utils/routeParams'

import { newPromptGenerationRoutePath } from './promptGenerationRoutes'
import { usePromptGenerationStyles } from './PromptGeneration.styles'

interface RegenerateDatasetLinkProps {
  readonly selectionKey: string
}

/** Links to a pre-filled generation form, shown only for datasets a known generator made. */
export default function RegenerateDatasetLink({ selectionKey }: RegenerateDatasetLinkProps) {
  const styles = usePromptGenerationStyles()
  const lookup = useGenerationRequestFromDataset(isNamedDatasetSelectionKey(selectionKey) ? selectionKey : null)
  if (lookup.error !== null) {
    return (
      <Text size={200} className={styles.muted} data-testid="regenerate-dataset-unavailable">
        Couldn't check whether this dataset can be regenerated: {lookup.error}{' '}
        <Button appearance="transparent" size="small" onClick={lookup.retry}>
          Check again
        </Button>
      </Text>
    )
  }
  if (lookup.source === null) return null
  return (
    <Link
      className={styles.secondaryLink}
      to={newPromptGenerationRoutePath({ generator: lookup.source.generator, selectionKey })}
      data-testid="regenerate-dataset-link"
    >
      <ArrowRepeatAllRegular aria-hidden /> Edit &amp; regenerate
    </Link>
  )
}
