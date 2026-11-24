import { useState } from 'react'

import { defaultTargetSelection } from '@/components/PromptGeneration/generationFormHelpers'
import type { PromptGeneratorDescriptor, TargetInstance } from '@/types'

export interface GenerationTargetSelection {
  /** The target registry name chosen for each role. */
  readonly selection: Record<string, string>
  readonly choose: (selection: Record<string, string>) => void
}

/**
 * Targets for a new generation: the source's targets when it recorded some, else the default target
 * for every role until the user chooses. Defaults follow the target registry while it loads, so a
 * registry that loads after the form still preselects them, and a user's choice is never replaced.
 */
export function useGenerationTargetSelection(
  descriptor: PromptGeneratorDescriptor,
  seedTargets: Readonly<Record<string, string>> | undefined,
  defaultTarget: TargetInstance | null,
): GenerationTargetSelection {
  const [chosen, setChosen] = useState<Record<string, string> | null>(() => (seedTargets ? { ...seedTargets } : null))
  return { selection: chosen ?? defaultTargetSelection(descriptor, defaultTarget), choose: setChosen }
}
