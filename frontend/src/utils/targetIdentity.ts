import type { AttackTargetResolutionStatus, TargetInfo, TargetInstance } from '../types'

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
export function targetIdentifierHash(target: TargetInstance): string {
  return target.identifier.hash
}

export type TargetHashResolution =
  | { status: 'resolved'; target: TargetInstance }
  | { status: 'unavailable' | 'ambiguous' }

/**
 * Resolves a persisted canonical identifier hash to one registered root target.
 *
 * Inner targets are intentionally excluded: a composite attack is bound to the
 * composite's identifier, not to one of its children.
 */
export function resolveTargetByIdentifierHash(
  identifierHash: string,
  targets: TargetInstance[],
): TargetHashResolution {
  if (!identifierHash) return { status: 'unavailable' }

  const uniqueTargets = new Map<string, TargetInstance>()
  for (const target of targets) {
    uniqueTargets.set(target.target_registry_name, target)
  }
  const matches = [...uniqueTargets.values()].filter(
    (target) => targetIdentifierHash(target) === identifierHash,
  )

  if (matches.length === 1) {
    return { status: 'resolved', target: matches[0] }
  }
  return { status: matches.length === 0 ? 'unavailable' : 'ambiguous' }
}

/** Whether persisted attack target information identifies the active target. */
export function targetInfoMatchesTarget(targetInfo: TargetInfo, target: TargetInstance): boolean {
  return targetInfo.identifier_hash === targetIdentifierHash(target)
}

/** Whether target resolution must keep an existing attack read-only. */
export function isTargetResolutionBlocking(status: AttackTargetResolutionStatus): boolean {
  return status === 'loading'
    || status === 'unavailable'
    || status === 'ambiguous'
    || status === 'error'
    || status === 'legacy'
}
