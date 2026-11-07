import type { TargetInstance } from '@/types'

/** TextTarget prints to the SDK host's stream rather than interacting with a GUI endpoint. */
export function isGuiTargetType(targetType: string): boolean {
  return targetType !== 'TextTarget'
}

export function isGuiTarget(target: Pick<TargetInstance, 'identifier' | 'inner_targets'>): boolean {
  return isGuiTargetType(target.identifier.class_name)
    && (target.inner_targets ?? []).every(isGuiTarget)
}
