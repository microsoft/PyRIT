import { Button } from '@fluentui/react-components'
import { FolderOpenRegular } from '@fluentui/react-icons'

import { useDatasetActionsStyles } from './DatasetActions.styles'

const CATALOG_PATH = '/datasets'

/** Catalog actions beside a scenario's dataset selection. They open in a new tab, so unsaved form edits stay. */
export default function DatasetActions() {
  const styles = useDatasetActionsStyles()
  return (
    <div className={styles.root} role="group" aria-label="Dataset actions">
      <Button as="a" href={CATALOG_PATH} target="_blank" rel="noopener noreferrer" role="link" appearance="secondary"
        icon={<FolderOpenRegular />}>
        Browse datasets
      </Button>
    </div>
  )
}
