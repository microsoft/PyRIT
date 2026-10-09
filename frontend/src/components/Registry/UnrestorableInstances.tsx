import type { MouseEvent } from 'react'

import {
  Button,
  MessageBar,
  MessageBarBody,
  MessageBarTitle,
  Text,
  useRestoreFocusTarget,
} from '@fluentui/react-components'
import { DeleteRegular } from '@fluentui/react-icons'

import type { UnrestorableInstance } from '@/types'

import { useUnrestorableInstancesStyles } from './UnrestorableInstances.styles'

interface UnrestorableInstancesProps {
  /** Singular noun for the instances listed, such as "target". */
  noun: string
  instances: UnrestorableInstance[]
  /** Why the saved instances could not be read at all, if the store was unavailable. */
  restoreError: string | null
  onDelete: (event: MouseEvent<HTMLButtonElement>, instance: UnrestorableInstance) => void
}

/** Explains which saved instances the backend could not rebuild, so they do not silently disappear. */
export default function UnrestorableInstances({
  noun,
  instances,
  restoreError,
  onDelete,
}: UnrestorableInstancesProps) {
  const styles = useUnrestorableInstancesStyles()
  const restoreFocusTarget = useRestoreFocusTarget()
  const title = `Saved ${noun}s that were not restored`

  return (
    <>
      {restoreError && (
        <MessageBar intent="error" layout="multiline">
          <MessageBarBody>
            <MessageBarTitle>Saved {noun}s could not be loaded</MessageBarTitle>
            {restoreError}
          </MessageBarBody>
        </MessageBar>
      )}
      {instances.length > 0 && (
        <MessageBar intent="warning" layout="multiline">
          <MessageBarBody>
            <MessageBarTitle>{title}</MessageBarTitle>
            <ul className={styles.list} aria-label={title}>
              {instances.map((instance) => (
                <li key={instance.name} className={styles.item}>
                  <div className={styles.details}>
                    <Text weight="semibold">{instance.name}</Text>
                    {instance.type && <Text size={200} className={styles.type}>{instance.type}</Text>}
                    <Text size={200} className={styles.reason}>{instance.reason}</Text>
                  </div>
                  {instance.version && (
                    <Button
                      {...restoreFocusTarget}
                      className={styles.deleteButton}
                      appearance="subtle"
                      icon={<DeleteRegular />}
                      aria-label={`Delete saved ${noun} ${instance.name}`}
                      onClick={(event) => onDelete(event, instance)}
                    >
                      Delete
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          </MessageBarBody>
        </MessageBar>
      )}
    </>
  )
}
