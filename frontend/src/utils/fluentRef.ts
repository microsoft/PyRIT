import type { Ref } from 'react'

/**
 * Adapts a DOM ref for a Fluent UI v9 component's `ref` prop.
 *
 * Under `exactOptionalPropertyTypes`, Fluent v9 slot props infer the root ref as
 * `Ref<never>`, so an ordinary element ref no longer type-checks
 * (microsoft/fluentui#25993). This is a type-only bridge: the ref is passed through
 * unchanged, and the parameter type still rejects anything that is not an element ref.
 */
export function fluentRef<T extends Element>(ref: Ref<T> | undefined): Ref<never> | undefined {
  return ref as Ref<never> | undefined
}
