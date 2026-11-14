import type { ScenarioActiveConversation } from '@/types'

import { scenariosApi } from './api'

/** Resolve SDK-recorded ownership without creating an attack result or inferring an outcome. */
export async function readRecordedScanConversations(
  scenarioResultId: string, attackResultId: string, conversationId: string,
): Promise<ScenarioActiveConversation[] | null> {
  const progress = await scenariosApi.getRunProgress(scenarioResultId)
  if (progress.run.scenario_result_id !== scenarioResultId) {
    throw new Error('The server returned a different scan while loading recorded conversation evidence.')
  }
  const records = (progress.recorded_conversations ?? []).filter((record: ScenarioActiveConversation) => record.attack_result_id === attackResultId)
  if (!records.some((record: ScenarioActiveConversation) => record.conversation_id === conversationId)) return null
  // The scoped read also verifies stored conversation metadata against the SDK-owned run provenance.
  await scenariosApi.getRunConversation(scenarioResultId, conversationId)
  return [...new Map(records.map((record: ScenarioActiveConversation) => [record.conversation_id, record])).values()]
}
