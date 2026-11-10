import type {
  PromptGenerationEstimate,
  PromptGenerationProgress,
  PromptGenerationSummary,
  PromptGenerationStatus,
  PromptGeneratorDescriptor,
} from '@/types'

type BadgeColor = 'informative' | 'brand' | 'success' | 'danger' | 'warning'

const ACTIVE_STATUSES: ReadonlySet<PromptGenerationStatus> = new Set(['queued', 'running', 'saving'])
const RETRYABLE_STATUSES: ReadonlySet<PromptGenerationStatus> = new Set(['failed', 'cancelled', 'interrupted'])
const CANCELLABLE_STATUSES: ReadonlySet<PromptGenerationStatus> = new Set(['queued', 'running', 'awaiting_review'])

export const GENERATION_STATUS_LABELS: Record<PromptGenerationStatus, string> = {
  queued: 'Queued',
  running: 'Running',
  awaiting_review: 'Review needed',
  saving: 'Saving',
  completed: 'Completed',
  failed: 'Failed',
  cancelled: 'Cancelled',
  interrupted: 'Interrupted',
}

export const GENERATION_STATUS_COLORS: Record<PromptGenerationStatus, BadgeColor> = {
  queued: 'informative',
  running: 'brand',
  awaiting_review: 'warning',
  saving: 'brand',
  completed: 'success',
  failed: 'danger',
  cancelled: 'warning',
  interrupted: 'danger',
}

/** Matches the server's dataset name rule. */
export const DATASET_NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/
const MAX_DATASET_NAME_LENGTH = 128
const VERSION_SUFFIX_PATTERN = /^(.*)_v(\d+)$/

/** Generations the server is working on; their pages poll. */
export function isActiveGenerationStatus(status: PromptGenerationStatus): boolean {
  return ACTIVE_STATUSES.has(status)
}

export function isRetryableGenerationStatus(status: PromptGenerationStatus): boolean {
  return RETRYABLE_STATUSES.has(status)
}

export function isCancellableGenerationStatus(status: PromptGenerationStatus): boolean {
  return CANCELLABLE_STATUSES.has(status)
}

export function generationStatusLabel(status: PromptGenerationStatus): string {
  return GENERATION_STATUS_LABELS[status] ?? status
}

export function datasetNameError(name: string): string | null {
  if (!name.trim()) return 'Enter a dataset name.'
  if (!DATASET_NAME_PATTERN.test(name)) {
    return 'Dataset names start with a letter or digit and use only letters, digits, ".", "_", and "-" (at most 128 characters).'
  }
  return null
}

/** Turns free text, such as a behavior name, into a valid dataset name. */
export function datasetNameFromText(text: string): string {
  const cleaned = text.trim().replace(/[^A-Za-z0-9_.-]+/g, '_').replace(/^[^A-Za-z0-9]+/, '')
  return cleaned.slice(0, MAX_DATASET_NAME_LENGTH)
}

/** Suggests the next name for a regenerated dataset: `name` becomes `name_v2`, `name_v2` becomes `name_v3`. */
export function nextDatasetName(name: string): string {
  const match = VERSION_SUFFIX_PATTERN.exec(name)
  const suffix = `_v${match ? Number(match[2]) + 1 : 2}`
  const base = datasetNameFromText(match ? match[1] ?? '' : name) || 'dataset'
  // Shorten the base, not the suffix, so a maximum-length name still gets a new name.
  return `${base.slice(0, MAX_DATASET_NAME_LENGTH - suffix.length)}${suffix}`
}

export function generatorDisplayName(
  descriptors: readonly PromptGeneratorDescriptor[],
  generator: string,
): string {
  return descriptors.find((descriptor) => descriptor.name === generator)?.display_name ?? generator
}

export function withIndefiniteArticle(text: string): string {
  return /^[aeiou]/i.test(text) ? `an ${text}` : `a ${text}`
}

export function formatCount(value: number): string {
  return value.toLocaleString('en-US')
}

export function formatRange(minimum: number, maximum: number): string {
  return minimum === maximum ? formatCount(minimum) : `${formatCount(minimum)}–${formatCount(maximum)}`
}

export function stageLabel(descriptor: PromptGeneratorDescriptor | undefined, stage: string): string {
  return descriptor?.stages.find((item) => item.name === stage)?.label ?? stage
}

/** One line describing where a generation is, such as "Generate seed groups · 12 of 50". */
export function progressText(
  status: PromptGenerationStatus,
  progress: PromptGenerationProgress,
  descriptor: PromptGeneratorDescriptor | undefined,
): string {
  const parts: string[] = []
  if (status !== 'completed' && progress.current_stage) {
    parts.push(stageLabel(descriptor, progress.current_stage))
  }
  if (progress.items_total > 0) {
    parts.push(`${formatCount(progress.items_completed)} of ${formatCount(progress.items_total)}`)
  }
  return parts.length > 0 ? parts.join(' · ') : generationStatusLabel(status)
}

/** Sums estimates for several datasets generated together. */
export function combineEstimates(estimates: readonly PromptGenerationEstimate[]): PromptGenerationEstimate | null {
  if (estimates.length === 0) return null
  if (estimates.length === 1) return estimates[0] ?? null
  const notes = [...new Set(estimates.flatMap((estimate) => estimate.notes))]
  return {
    stages: estimates.flatMap((estimate) => estimate.stages),
    exact_calls: estimates.every((estimate) => estimate.exact_calls),
    notes,
    min_calls: estimates.reduce((total, estimate) => total + estimate.min_calls, 0),
    max_calls: estimates.reduce((total, estimate) => total + estimate.max_calls, 0),
    low_tokens: estimates.reduce((total, estimate) => total + estimate.low_tokens, 0),
    high_tokens: estimates.reduce((total, estimate) => total + estimate.high_tokens, 0),
  }
}

/** One row of the generation list: a generation on its own, or a group with its members. */
export type GenerationListRow =
  | { readonly kind: 'generation'; readonly generation: PromptGenerationSummary }
  | { readonly kind: 'group'; readonly groupId: string; readonly label: string; readonly items: PromptGenerationSummary[] }

/**
 * Groups generations that share a group, at the position of the group's newest member. Members are
 * listed in the order they were requested, which their creation times record.
 */
export function generationListRows(items: readonly PromptGenerationSummary[]): GenerationListRow[] {
  const rows: GenerationListRow[] = []
  const groups = new Map<string, PromptGenerationSummary[]>()
  for (const item of items) {
    if (!item.group_id) {
      rows.push({ kind: 'generation', generation: item })
      continue
    }
    const members = groups.get(item.group_id)
    if (members) {
      members.push(item)
      continue
    }
    const created = [item]
    groups.set(item.group_id, created)
    rows.push({ kind: 'group', groupId: item.group_id, label: item.group_label ?? item.group_id, items: created })
  }
  for (const members of groups.values()) {
    members.sort((left, right) => Date.parse(left.created_at) - Date.parse(right.created_at))
  }
  return rows
}

export interface GenerationStatusCounts {
  readonly total: number
  readonly completed: number
  readonly active: number
  readonly awaitingReview: number
  readonly failed: number
}

export function countGenerationStatuses(items: readonly PromptGenerationSummary[]): GenerationStatusCounts {
  return {
    total: items.length,
    completed: items.filter((item) => item.status === 'completed').length,
    active: items.filter((item) => isActiveGenerationStatus(item.status)).length,
    awaitingReview: items.filter((item) => item.status === 'awaiting_review').length,
    failed: items.filter((item) => isRetryableGenerationStatus(item.status)).length,
  }
}

/** The status a set of generations needs attention for first, such as a review, then running work. */
export function aggregateGenerationStatus(items: readonly PromptGenerationSummary[]): PromptGenerationStatus {
  const statuses = new Set(items.map((item) => item.status))
  const order: readonly PromptGenerationStatus[] = [
    'awaiting_review', 'running', 'saving', 'queued', 'failed', 'interrupted', 'cancelled',
  ]
  return order.find((status) => statuses.has(status)) ?? 'completed'
}
