import type { PromptGenerationDetail, PromptGeneratorDescriptor, TargetInstance } from '@/types'

/** Initial values for a generator form, from a template, an earlier generation, or a stored dataset. */
export interface PromptGenerationFormSeed {
  readonly request?: Record<string, unknown>
  readonly datasetName?: string
  readonly targets?: Record<string, string>
  readonly pauseForReview?: boolean
  /** A generator-defined template name, such as an ASSERT library behavior. */
  readonly template?: string
  /** A generator-defined template for several datasets, such as an ASSERT suite. */
  readonly suite?: string
}

export interface PromptGenerationFormProps {
  readonly descriptor: PromptGeneratorDescriptor
  readonly targets: TargetInstance[]
  readonly targetsLoading: boolean
  readonly targetsError: string | null
  /** The default adversarial target, pre-selected for every target role of a new generation. */
  readonly defaultTarget: TargetInstance | null
  readonly seed: PromptGenerationFormSeed
  /** True while generator metadata reloads; the form keeps its input but can't submit. */
  readonly metadataRefreshing?: boolean
}

export interface PromptGenerationReviewProps {
  readonly generation: PromptGenerationDetail
  readonly descriptor: PromptGeneratorDescriptor | undefined
  /** Reports a generation returned by an action, such as continuing after review. */
  readonly onGenerationChange: (generation: PromptGenerationDetail) => void
}

export interface PromptGenerationSummaryProps {
  readonly summary: Record<string, unknown>
}
