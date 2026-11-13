import AttackAttemptDetails from '@/components/AttackResults/AttackAttemptDetails'
import type { ScenarioRunAttempt } from '@/hooks/useScenarioRunAttackDetails'
import type {
  ScenarioActiveConversation,
  ScenarioHierarchyGroup,
  ScenarioHierarchyNode,
  ScenarioProgressSummary,
  ScenarioRunPlan,
} from '@/types'
import { attackConversationRoutePath } from '@/utils/routeParams'

/** What a scan run knows about its attacks, shared by every place that shows one attack's details. */
export interface ScenarioRunDetailsContext {
  readonly scenarioResultId: string
  /** The run's Operation, offered first when attaching an attack to a finding. */
  readonly operationId?: string | null
  readonly plan: ScenarioRunPlan | null
  readonly summary: ScenarioProgressSummary
  readonly activeConversations: readonly ScenarioActiveConversation[]
  readonly hierarchyGroups: readonly ScenarioHierarchyGroup[]
  readonly seedObjectives: ReadonlyMap<string, string>
  /** Router state carried to the conversation, so its back link returns here. */
  readonly conversationState: unknown
}

interface ScenarioAttackDetailsProps {
  readonly context: ScenarioRunDetailsContext
  readonly attempt: ScenarioRunAttempt | null
  /** The planned group to describe while the attack has no attempt yet. */
  readonly atomicGroupId?: string | null
  /** The hierarchy node the details were opened from, which may carry the objective. */
  readonly hierarchySelection?: ScenarioHierarchyNode | null
  readonly preparing?: boolean
  readonly framed?: boolean
}

/** One scan attack's details, resolved from the run's plan, progress, and saved hierarchy. */
export default function ScenarioAttackDetails({
  context, attempt, atomicGroupId = null, hierarchySelection = null, preparing = false, framed = false,
}: ScenarioAttackDetailsProps) {
  const { scenarioResultId, plan, summary, activeConversations, hierarchyGroups, seedObjectives } = context
  const groupId = attempt && 'atomic_group_id' in attempt ? attempt.atomic_group_id : atomicGroupId
  const atomicGroup = groupId ? plan?.atomic_groups.find((group) => group.id === groupId) : undefined
  const active = attempt
    ? activeConversations.find((conversation) => conversation.attack_result_id === attempt.attack_result_id)
    : undefined
  const seedGroupId = attempt?.seed_group_id ?? atomicGroup?.seed_group_ids[0]
  const objective = (attempt && hierarchySelection?.attack_result_id === attempt.attack_result_id ? hierarchySelection.objective : null)
    ?? (attempt && 'objective' in attempt ? attempt.objective : null)
    ?? (seedGroupId ? seedObjectives.get(seedGroupId) : null)
    ?? null
  const techniqueDetails = active?.technique_details
    ?? summary.atomic_groups.find((group) => group.id === groupId)?.technique_details
    ?? hierarchyGroups.find((group) => group.atomic_group_id === groupId)?.technique
    ?? null
  const conversationId = active?.conversation_id ?? attempt?.conversation_id
  return (
    <AttackAttemptDetails
      attempt={attempt}
      preparing={preparing}
      objective={objective}
      atomicGroup={atomicGroup}
      techniqueDetails={techniqueDetails}
      objectiveScorer={summary.objective_scorer}
      conversationPath={attempt && conversationId
        ? attackConversationRoutePath(attempt.attack_result_id, conversationId, scenarioResultId)
        : null}
      conversationState={context.conversationState}
      findingOperationId={context.operationId}
      framed={framed}
    />
  )
}
