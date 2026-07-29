import type { RegisteredInitializer } from '@/types'

/**
 * Resolve a settings entry's `initializer_name` to its catalog definition.
 *
 * Settings reference an initializer by name; the catalog (from `listRegistered`)
 * is the single source of truth for display metadata. When a persisted name is no
 * longer registered, return a placeholder so the row still renders.
 */
export function resolveRegisteredInitializer(
  initializerName: string,
  registeredInitializers: RegisteredInitializer[],
): RegisteredInitializer {
  const match = registeredInitializers.find((item) => item.initializer_name === initializerName)
  if (match) {
    return match
  }

  return {
    initializer_name: initializerName,
    initializer_type: 'UnknownInitializer',
    description: 'Initializer is no longer registered.',
    required_env_vars: [],
    supported_parameters: [],
  }
}
