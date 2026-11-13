import { useMemo, useRef, useState } from 'react'

import { useLocation, useNavigate } from 'react-router'

import type { ScenarioActiveConversation, ScenarioHierarchyNode, ScenarioProgressResult } from '@/types'
import { scenarioRunAttackRoutePath, scenarioRunRoutePath } from '@/utils/routeParams'

export type ScenarioRunAttempt = ScenarioProgressResult | ScenarioHierarchyNode | ScenarioActiveConversation

interface UseScenarioRunAttackDetailsOptions {
  readonly scenarioResultId: string
  /** The attack named by the route, if any. */
  readonly attackResultId: string | null
  readonly results: readonly ScenarioProgressResult[]
  readonly activeConversations: readonly ScenarioActiveConversation[]
  /** Receives focus on close when the control that opened the details is gone. */
  readonly fallbackFocus: () => HTMLElement | null
}

export interface ScenarioRunAttackDetails {
  readonly selectedAttempt: ScenarioRunAttempt | null
  /** The hierarchy node the details were opened from, which may carry the objective. */
  readonly hierarchySelection: ScenarioHierarchyNode | null
  readonly open: (attempt: ScenarioRunAttempt, trigger: HTMLElement | null) => void
  readonly close: () => void
}

/** The attack shown in the run's details dialog, addressed by route so it survives reloads and back navigation. */
export function useScenarioRunAttackDetails({
  scenarioResultId,
  attackResultId,
  results,
  activeConversations,
  fallbackFocus,
}: UseScenarioRunAttackDetailsOptions): ScenarioRunAttackDetails {
  const navigate = useNavigate()
  const location = useLocation()
  const trigger = useRef<HTMLElement | null>(null)
  const [hierarchySelection, setHierarchySelection] = useState<ScenarioHierarchyNode | null>(null)

  // A hierarchy node can be opened before the paged progress feed has delivered it.
  const selectedAttempt = useMemo<ScenarioRunAttempt | null>(
    () =>
      results.find((attempt) => attempt.attack_result_id === attackResultId) ??
      activeConversations.find((conversation) => conversation.attack_result_id === attackResultId) ??
      (hierarchySelection?.attack_result_id === attackResultId ? hierarchySelection : null),
    [attackResultId, results, activeConversations, hierarchySelection],
  )

  return {
    selectedAttempt,
    hierarchySelection,
    open: (attempt: ScenarioRunAttempt, opener: HTMLElement | null): void => {
      trigger.current = opener
      setHierarchySelection('result_role' in attempt ? attempt : null)
      navigate(scenarioRunAttackRoutePath(scenarioResultId, attempt.attack_result_id), { state: location.state })
    },
    close: (): void => {
      navigate(scenarioRunRoutePath(scenarioResultId), { replace: true, state: location.state })
      requestAnimationFrame(() => {
        const focusTarget = trigger.current?.isConnected ? trigger.current : fallbackFocus()
        focusTarget?.focus()
      })
    },
  }
}
