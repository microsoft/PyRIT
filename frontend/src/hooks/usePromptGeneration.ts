import { promptGenerationsApi } from '@/services/api'
import { isActiveGenerationStatus } from '@/components/PromptGeneration/promptGenerationPresentation'
import type { PromptGenerationDetail, PromptGenerationSummary } from '@/types'

import { usePolledRecord, type UsePolledRecordResult } from './usePolledRecord'

export const PROMPT_GENERATION_POLL_INTERVAL_MS = 2_000

function loadGeneration(generationId: string, signal: AbortSignal): Promise<PromptGenerationDetail> {
  return promptGenerationsApi.get(generationId, signal)
}

function generationIsActive(generation: PromptGenerationDetail): boolean {
  return isActiveGenerationStatus(generation.status)
}

function itemsAreActive(items: readonly PromptGenerationSummary[]): boolean {
  return items.some((item) => isActiveGenerationStatus(item.status))
}

const ALL_GENERATIONS_KEY = 'all'

async function loadGenerationList(_key: string, signal: AbortSignal): Promise<PromptGenerationSummary[]> {
  return (await promptGenerationsApi.list(signal)).items
}

/** Loads one generation and polls it while the server is working on it. */
export function usePromptGeneration(generationId: string): UsePolledRecordResult<PromptGenerationDetail> {
  return usePolledRecord(generationId, loadGeneration, generationIsActive, PROMPT_GENERATION_POLL_INTERVAL_MS)
}

/** Lists generations newest first and polls while any is active. */
export function usePromptGenerationList(): UsePolledRecordResult<PromptGenerationSummary[]> {
  return usePolledRecord(ALL_GENERATIONS_KEY, loadGenerationList, itemsAreActive, PROMPT_GENERATION_POLL_INTERVAL_MS)
}
