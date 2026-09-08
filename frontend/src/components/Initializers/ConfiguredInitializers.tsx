import { Text } from '@fluentui/react-components'

import type { ConfiguredInitializerSetting, RegisteredInitializer } from '@/types'

import { formatInitializerParameters } from './initializerFormatting'
import { resolveRegisteredInitializer } from './initializerLookup'
import { useInitializersStyles } from './Initializers.styles'

interface ConfiguredInitializersProps {
  items: ConfiguredInitializerSetting[]
  registeredInitializers: RegisteredInitializer[]
}

export default function ConfiguredInitializers({
  items,
  registeredInitializers,
}: ConfiguredInitializersProps) {
  const styles = useInitializersStyles()

  return (
    <section className={styles.section} aria-labelledby="configured-initializers-heading">
      <div className={styles.sectionHeader}>
        <Text as="h2" id="configured-initializers-heading" size={500} weight="semibold">
          Configured initializers
        </Text>
        <Text size={300} className={styles.metadataText}>
          Read-only startup sequence from the active .pyrit_conf.
        </Text>
      </div>
      {items.length === 0 ? (
        <Text className={styles.emptyState}>No initializers are configured in .pyrit_conf.</Text>
      ) : (
        <div className={styles.configuredGroup} role="list" aria-label="Configured initializers">
          {items.map((item: ConfiguredInitializerSetting) => {
            const initializer = resolveRegisteredInitializer(item.initializer_name, registeredInitializers)
            const initializerKey = `${item.initializer_name}:${item.order_index}`
            return (
              <div
                key={initializerKey}
                className={styles.configuredGroupItem}
                role="listitem"
                data-testid={`configured-initializer-row-${item.order_index}`}
              >
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
