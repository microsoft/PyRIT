import { Button } from '@fluentui/react-components'
import { FolderOpenRegular } from '@fluentui/react-icons'
import { useLinkClickHandler } from 'react-router'

import { useDatasetActionsStyles } from './DatasetActions.styles'

const CATALOG_PATH = '/datasets'

/** Catalog actions beside a scenario's dataset selection. */
export default function DatasetActions() {
  const styles = useDatasetActionsStyles()
  const browse = useLinkClickHandler<HTMLAnchorElement>(CATALOG_PATH)
  return (
    <div className={styles.root} role="group" aria-label="Dataset actions">
      <Button as="a" href={CATALOG_PATH} onClick={browse} role="link" appearance="secondary" icon={<FolderOpenRegular />}>
        Browse datasets
      </Button>
    </div>
  )
}
