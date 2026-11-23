import { useState } from 'react'

import { useNavigate } from 'react-router'

import {
  PROMPT_GENERATIONS_PATH,
  promptGenerationRoutePath,
} from '@/components/PromptGeneration/promptGenerationRoutes'
import { promptGenerationsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { PromptGenerationBatchItem } from '@/types'

/** What a generator form submits: one dataset, or several that share settings. */
export type PromptGenerationSubmission =
  | { readonly kind: 'single'; readonly datasetName: string; readonly request: Record<string, unknown> }
  | { readonly kind: 'batch'; readonly label: string; readonly items: readonly PromptGenerationBatchItem[] }

export interface PromptGenerationSubmitInput {
  readonly generator: string
  readonly submission: PromptGenerationSubmission
  readonly targets: Record<string, string>
  readonly pauseForReview: boolean
}

export interface PromptGenerationSubmitState {
  readonly submitting: boolean
  readonly error: string | null
  readonly submit: (input: PromptGenerationSubmitInput) => Promise<void>
}

/** Queues a generation and opens its page, or queues a batch and opens the generation list. */
export function usePromptGenerationSubmit(): PromptGenerationSubmitState {
  const navigate = useNavigate()
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async ({ generator, submission, targets, pauseForReview }: PromptGenerationSubmitInput): Promise<void> => {
    setSubmitting(true)
    setError(null)
    try {
      if (submission.kind === 'batch') {
        await promptGenerationsApi.createBatch({
          generator,
          label: submission.label,
          items: [...submission.items],
          targets,
          pause_for_review: pauseForReview,
        })
        navigate(PROMPT_GENERATIONS_PATH)
        return
      }
      const created = await promptGenerationsApi.create({
        generator,
        dataset_name: submission.datasetName,
        request: submission.request,
        targets,
        pause_for_review: pauseForReview,
      })
      navigate(promptGenerationRoutePath(created.id))
    } catch (cause: unknown) {
      setError(toApiError(cause).detail)
      setSubmitting(false)
    }
  }

  return { submitting, error, submit }
}
