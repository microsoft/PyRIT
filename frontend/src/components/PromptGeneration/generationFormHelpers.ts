import type {
  PromptGenerationPreflightResponse,
  PromptGeneratorDescriptor,
  PromptGeneratorTargetRole,
  TargetInstance,
} from '@/types'

/** Limits the targets offered for one role and says why. */
export interface TargetRoleFilter {
  readonly allows: (target: TargetInstance) => boolean
  readonly reason: string
}

/** The selected target for each role must be registered and pass its filter. */
export function targetSelectionErrors(
  roles: readonly PromptGeneratorTargetRole[],
  targets: readonly TargetInstance[],
  value: Readonly<Record<string, string>>,
  filters: Readonly<Record<string, TargetRoleFilter>> = {},
): string[] {
  const errors: string[] = []
  for (const role of roles) {
    const name = value[role.role]
    if (!name) continue
    const target = targets.find((item) => item.target_registry_name === name)
    const filter = filters[role.role]
    if (!target) errors.push(`${role.label}: "${name}" is not a registered target.`)
    else if (filter && !filter.allows(target)) errors.push(`${role.label}: ${filter.reason}`)
  }
  return errors
}

/** Initial target selection: the seed's choices, else the default target for required roles. */
export function initialTargetSelection(
  descriptor: PromptGeneratorDescriptor,
  seedTargets: Readonly<Record<string, string>> | undefined,
  defaultTarget: TargetInstance | null,
): Record<string, string> {
  if (seedTargets) return { ...seedTargets }
  const selection: Record<string, string> = {}
  if (!defaultTarget) return selection
  for (const role of descriptor.target_roles) {
    if (role.required) selection[role.role] = defaultTarget.target_registry_name
  }
  return selection
}

export function hasReviewableStage(descriptor: PromptGeneratorDescriptor): boolean {
  return descriptor.stages.some((stage) => stage.reviewable)
}

export function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

export function readString(value: unknown, fallback: string = ''): string {
  return typeof value === 'string' ? value : fallback
}

export function readBoolean(value: unknown, fallback: boolean): boolean {
  return typeof value === 'boolean' ? value : fallback
}

export function readStringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

/** A stored number as form text, or the fallback when it is missing. */
export function readNumberText(value: unknown, fallback: number): string {
  return typeof value === 'number' && Number.isFinite(value) ? String(value) : String(fallback)
}

/** Parses form text as a whole number within bounds, or returns null. */
export function parseWholeNumber(text: string, minimum: number, maximum: number): number | null {
  const trimmed = text.trim()
  const value = Number(trimmed)
  return trimmed !== '' && Number.isSafeInteger(value) && value >= minimum && value <= maximum ? value : null
}

export interface PreflightProblems {
  /** Problems that block starting, labelled by dataset when there are several. */
  readonly messages: string[]
  readonly blocksGenerate: boolean
}

/** Summarizes preflight results for the datasets in a form. */
export function preflightProblems(
  results: readonly PromptGenerationPreflightResponse[],
  datasetNames: readonly string[],
): PreflightProblems {
  const messages: string[] = []
  let blocksGenerate = false
  for (const [index, result] of results.entries()) {
    const name = datasetNames[index] ?? ''
    const prefix = results.length > 1 && name ? `${name}: ` : ''
    if (!result.dataset_name_available) {
      messages.push(`${prefix}A dataset named "${name}" already exists. Choose another name.`)
      blocksGenerate = true
    }
    if (!result.valid) blocksGenerate = true
    for (const error of result.errors) messages.push(`${prefix}${error}`)
  }
  return { messages: [...new Set(messages)], blocksGenerate }
}
