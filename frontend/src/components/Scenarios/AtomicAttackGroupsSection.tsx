import { useMemo, useState, type Ref } from 'react'

import { Button, Text } from '@fluentui/react-components'
import { ChevronDownRegular, ChevronRightRegular } from '@fluentui/react-icons'

import type { UseScenarioResultHierarchyResult } from '@/hooks/useScenarioResultHierarchy'
import type { ScenarioHierarchyNode, ScenarioProgressResult, ScenarioProgressSummary, ScenarioRunState } from '@/types'

import AttackExecutionTable from './AttackExecutionTable'
import { useScenarioRunPageStyles } from './ScenarioRunPage.styles'
import ScenarioResultHierarchy from './ScenarioResultHierarchy'
import { hierarchyDisplayGroups } from './scenarioHierarchy'

const MAX_VISIBLE_ATTEMPTS_PER_GROUP = 100
const AUTO_EXPAND_GROUP_LIMIT = 20

interface AtomicAttackGroupsSectionProps {
  readonly scenarioResultId: string
  readonly runStatus: ScenarioRunState
  readonly summary: ScenarioProgressSummary
  readonly results: readonly ScenarioProgressResult[]
  readonly hierarchy: UseScenarioResultHierarchyResult
  readonly seedObjectives: Map<string, string>
  readonly onOpenDetails: (attempt: ScenarioProgressResult | ScenarioHierarchyNode, trigger: HTMLElement) => void
  /** The section toggle, which receives focus when an opened attack is no longer on the page. */
  readonly toggleRef: Ref<HTMLButtonElement>
}

/** Every display group's progress, expandable to its attack executions. */
export default function AtomicAttackGroupsSection({
  scenarioResultId, runStatus, summary, results, hierarchy, seedObjectives, onOpenDetails, toggleRef,
}: AtomicAttackGroupsSectionProps) {
  const styles = useScenarioRunPageStyles()
  const [sectionExpandedChoice, setSectionExpandedChoice] = useState<boolean | null>(null)
  const [expandedGroupChoices, setExpandedGroupChoices] = useState<Map<string, boolean>>(new Map())
  // Attempts are grouped by atomic group id, then truncated per group, so expanding an
  // older group still shows its own executions rather than an empty table.
  const attemptsByGroupId = useMemo(() => {
    const groupedAttempts = new Map<string, ScenarioProgressResult[]>()
    for (let index = results.length - 1; index >= 0; index -= 1) {
      const attempt = results[index]
      const existing = groupedAttempts.get(attempt.atomic_group_id)
      if (existing) {
        existing.push(attempt)
      } else {
        groupedAttempts.set(attempt.atomic_group_id, [attempt])
      }
    }
    return groupedAttempts
  }, [results])

  const displayGroups = hierarchyDisplayGroups(summary.display_groups ?? summary.techniques, hierarchy.roots.groups)
  const producerCounts = summary.overall.producer_counts
  const executionCount = producerCounts
    ? producerCounts.target_facing.attempts + producerCounts.orchestration.attempts + producerCounts.unknown.attempts
    : results.length
  const defaultExpanded = displayGroups.length <= AUTO_EXPAND_GROUP_LIMIT
  const sectionExpanded = sectionExpandedChoice ?? defaultExpanded

  const toggleDisplayGroup = (groupId: string): void => {
    setExpandedGroupChoices((current) => new Map(current).set(groupId, !(current.get(groupId) ?? defaultExpanded)))
  }

  return (
    <section className={styles.section} aria-labelledby="atomic-groups-heading">
      <div className={styles.sectionHeading}>
        <div className={styles.objectivesHeading}>
          <Text as="h2" id="atomic-groups-heading" size={500} weight="semibold">
            Atomic attack groups
          </Text>
          <Button
            appearance="subtle"
            className={styles.touchTarget}
            icon={sectionExpanded ? <ChevronDownRegular /> : <ChevronRightRegular />}
            aria-expanded={sectionExpanded}
            aria-controls="atomic-groups-panel"
            aria-label={`${sectionExpanded ? 'Collapse' : 'Expand'} atomic attack groups`}
            data-testid="toggle-atomic-groups-btn"
            ref={toggleRef}
            onClick={() => setSectionExpandedChoice(!sectionExpanded)}
          >
          </Button>
        </div>
        <Text className={styles.sectionHint}>
          {`${displayGroups.length.toLocaleString()} group${displayGroups.length === 1 ? '' : 's'}, ${executionCount.toLocaleString()} ${producerCounts ? '' : 'loaded '}execution${executionCount === 1 ? '' : 's'}`}
        </Text>
      </div>
      <div id="atomic-groups-panel" hidden={!sectionExpanded}>
        {sectionExpanded && (
          <ScenarioResultHierarchy hierarchy={hierarchy} runStatus={runStatus}>
            {displayGroups.length === 0 ? (
              <Text className={styles.sectionHint}>No atomic attack groups have been persisted yet.</Text>
            ) : (
              <div className={styles.displayGroupList}>
                {displayGroups.map((group) => {
                  const attempts = (group.atomic_group_ids ?? [])
                    .flatMap((atomicGroupId) => attemptsByGroupId.get(atomicGroupId) ?? [])
                  const expanded = expandedGroupChoices.get(group.id) ?? defaultExpanded
                  const panelId = `atomic-group-${group.id.replace(/[^a-zA-Z0-9_-]/g, '-')}`
                  return (
                    <article key={group.id} className={styles.displayGroup}>
                      <div className={styles.displayGroupSummary}>
                        <Button
                          appearance="subtle"
                          className={styles.expandButton}
                          icon={expanded ? <ChevronDownRegular /> : <ChevronRightRegular />}
                          aria-expanded={expanded}
                          aria-controls={panelId}
                          aria-label={`${expanded ? 'Collapse' : 'Expand'} attacks in ${group.display_group}`}
                          onClick={() => toggleDisplayGroup(group.id)}
                        />
                        <span className={styles.displayGroupIdentity}>
                          <Text size={400} weight="semibold">{group.display_group}</Text>
                          <Text size={200} className={styles.sectionHint}>
                            {group.atomic_attack_names.join(', ')}
                          </Text>
                        </span>
                        <span className={styles.displayGroupMetrics}>
                          <DisplayGroupMetric label="Completed" value={formatCompletion(group.completed, group.planned)} />
                          <DisplayGroupMetric
                            label="Attack success"
                            value={formatSuccess(group.succeeded, group.completed, group.success_percentage)}
                          />
                          <DisplayGroupMetric label="Errors" value={String(group.errors)} />
                          <DisplayGroupMetric label="Retries" value={String(group.retries)} />
                        </span>
                      </div>
                      {expanded && (
                        <div id={panelId} className={styles.displayGroupPanel} aria-label={`${group.display_group} attack executions`}>
                          <AttackExecutionTable
                            scenarioResultId={scenarioResultId}
                            label={`${group.display_group} attack results`}
                            attempts={attempts.slice(0, MAX_VISIBLE_ATTEMPTS_PER_GROUP)}
                            totalAttempts={attempts.length}
                            roots={hierarchy.roots.groups
                              .filter((savedGroup) => group.atomic_group_ids.includes(savedGroup.atomic_group_id))
                              .flatMap((savedGroup) => savedGroup.roots)}
                            hierarchy={hierarchy}
                            seedObjectives={seedObjectives}
                            onOpenDetails={onOpenDetails}
                            showTimestamp={false}
                            showConversationLink
                            booleanOutcome
                          />
                        </div>
                      )}
                    </article>
                  )
                })}
              </div>
            )}
          </ScenarioResultHierarchy>
        )}
      </div>
    </section>
  )
}

function DisplayGroupMetric({ label, value }: { readonly label: string; readonly value: string }) {
  return (
    <div>
      <Text size={200}>{label}</Text>
      <Text weight="semibold">{value}</Text>
    </div>
  )
}

function formatCompletion(completed: number, planned: number | null): string {
  return planned === null ? `${completed}/total unavailable` : `${completed}/${planned}`
}

function formatSuccess(succeeded: number, completed: number, percentage: number | null): string {
  if (percentage === null) return completed === 0 ? 'Unavailable' : `${succeeded}/${completed}`
  return `${percentage}%`
}
