import type { TargetInstance } from '../types'

/**
 * Helpers for reading a target's identity off its embedded `identifier`.
 *
 * `TargetInstance` no longer mirrors identity as flat fields — class name,
 * endpoint, model name, and generation params all live on `identifier`. For
 * composite targets (RoundRobinTarget) the identifier itself carries no model
 * name, so the model helpers hoist a shared value from the inner targets when
 * they all agree (mirroring how the backend used to present it).
 */

function hoistFromInner(
  target: TargetInstance,
  pick: (t: TargetInstance) => string | null | undefined,
): string | null {
  const own = pick(target)
  if (own) return own

  const inners = target.inner_targets ?? []
  if (inners.length > 0) {
    const values = new Set(inners.map(pick).filter((value): value is string => Boolean(value)))
    if (values.size === 1) return [...values][0]
  }
  return null
}

/** The target class name (e.g., 'OpenAIChatTarget'). */
export function targetType(target: TargetInstance): string {
  return target.identifier.class_name
}

/** The deployment/model name, hoisted from inner targets for composite targets. */
export function targetModelName(target: TargetInstance): string | null {
  return hoistFromInner(target, (t) => t.identifier.model_name)
}

/** The underlying model name, hoisted from inner targets for composite targets. */
export function targetUnderlyingModelName(target: TargetInstance): string | null {
  return hoistFromInner(target, (t) => t.identifier.underlying_model_name)
}

/** The target endpoint URL, or null. */
export function targetEndpoint(target: TargetInstance): string | null {
  return (target.identifier.endpoint as string | null | undefined) ?? null
}

/** The ComponentIdentifier content hash used for duplicate detection. */
export function targetIdentifierHash(target: TargetInstance): string | null {
  return target.identifier.hash ?? null
}
