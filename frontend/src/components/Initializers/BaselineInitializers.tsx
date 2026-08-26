import { Text } from '@fluentui/react-components'

import type { BaselineInitializerSetting, RegisteredInitializer } from '@/types'

import { formatInitializerParameters } from './initializerFormatting'
import { resolveRegisteredInitializer } from './initializerLookup'
import { useInitializersStyles } from './Initializers.styles'

interface BaselineInitializersProps {
  items: BaselineInitializerSetting[]
  registeredInitializers: RegisteredInitializer[]
}

export default function BaselineInitializers({
  items,
  registeredInitializers,
}: BaselineInitializersProps) {
  const styles = useInitializersStyles()

  return (
    <section className={styles.section} aria-labelledby="baseline-initializers-heading">
      <div className={styles.sectionHeader}>
        <Text as="h2" id="baseline-initializers-heading" size={500} weight="semibold">
          Baseline initializers
        </Text>
        <Text size={300} className={styles.metadataText}>
          Read-only initializers from the .pyrit_conf baseline.
        </Text>
      </div>
      {items.length === 0 ? (
        <Text className={styles.emptyState}>No baseline initializers are configured.</Text>
      ) : (
        <div className={styles.baselineGroup} role="list" aria-label="Baseline initializers">
          {items.map((item: BaselineInitializerSetting) => {
            const initializer = resolveRegisteredInitializer(item.initializer_name, registeredInitializers)
            return (
              <div
                key={`${item.initializer_name}:${item.order_index}`}
                className={styles.baselineGroupItem}
                role="listitem"
                data-testid={`baseline-initializer-row-${item.initializer_name}`}
              >
                <div className={styles.baselineHeader}>
                  <div className={styles.titleGroup}>
                    <Text weight="semibold" size={400}>{item.initializer_name}</Text>
                    <Text size={300}>{initializer.description || 'No description available.'}</Text>
                    <Text size={200} className={styles.metadataText}>
                      Required env vars: {initializer.required_env_vars.length > 0
                        ? initializer.required_env_vars.join(', ')
                        : 'None'}
                    </Text>
                    <Text size={200} className={styles.metadataText}>Order: {item.order_index}</Text>
                  </div>
                </div>
                <div>
                  <Text weight="semibold" size={300}>Parameters</Text>
                  <pre className={styles.parametersBlock}>{formatInitializerParameters(item.parameters)}</pre>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}
