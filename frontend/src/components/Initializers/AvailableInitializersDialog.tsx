import { useState } from 'react'

import {
  Button,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
  DialogTrigger,
  Text,
} from '@fluentui/react-components'
import { AppsListRegular } from '@fluentui/react-icons'

import type { RegisteredInitializer } from '@/types'

import { formatSupportedParameterSummary } from './initializerFormatting'
import { useInitializersStyles } from './Initializers.styles'

interface AvailableInitializersDialogProps {
  registeredInitializers: RegisteredInitializer[]
  disabled?: boolean
}

export default function AvailableInitializersDialog({
  registeredInitializers,
  disabled = false,
}: AvailableInitializersDialogProps) {
  const styles = useInitializersStyles()
  const [open, setOpen] = useState(false)

  return (
    <Dialog open={open} onOpenChange={(_, data) => setOpen(data.open)}>
      <DialogTrigger disableButtonEnhancement>
        <Button
          appearance="secondary"
          icon={<AppsListRegular />}
          disabled={disabled}
          onClick={() => setOpen(true)}
        >
          Browse available initializers
        </Button>
      </DialogTrigger>
      <DialogSurface>
        <DialogBody>
          <DialogTitle>Available initializers</DialogTitle>
          <DialogContent>
            <Text size={300} className={styles.metadataText}>
              Every initializer registered with PyRIT. This is a read-only reference of what exists and the
              parameters each one accepts.
            </Text>
            {registeredInitializers.length === 0 ? (
              <Text className={styles.emptyState}>No registered initializers were found.</Text>
            ) : (
              <div className={styles.dialogList} role="list" aria-label="Available initializers">
                {registeredInitializers.map((initializer: RegisteredInitializer) => (
                  <div
                    key={initializer.initializer_name}
                    className={styles.availableCard}
                    role="listitem"
                    data-testid={`available-initializer-row-${initializer.initializer_name}`}
                  >
                    <div className={styles.titleGroup}>
                      <Text weight="semibold" size={400}>{initializer.initializer_name}</Text>
                      <Text size={300}>{initializer.description || 'No description available.'}</Text>
                      <Text size={200} className={styles.metadataText}>
                        Required env vars: {initializer.required_env_vars.length > 0
                          ? initializer.required_env_vars.join(', ')
                          : 'None'}
                      </Text>
                    </div>
                    <div>
                      <Text weight="semibold" size={300}>Parameters</Text>
                      <div className={styles.parameterSummaryList}>
                        {formatSupportedParameterSummary(initializer).map((summary: string) => (
                          <Text key={summary} size={200} className={styles.metadataText}>
                            {summary}
                          </Text>
                        ))}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </DialogContent>
          <DialogActions>
            <DialogTrigger disableButtonEnhancement>
              <Button appearance="secondary">Close</Button>
            </DialogTrigger>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  )
}
