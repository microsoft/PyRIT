import { Badge, Button, Tooltip } from '@fluentui/react-components'
import { AddRegular, DeleteRegular, EditRegular, WarningRegular } from '@fluentui/react-icons'

import CollapsibleCard from '@/components/CollapsibleCard'
import { formatScenarioCount, suiteNeedsAttention, type SuiteMember } from '@/components/ScenarioSuites/suiteMembers'
import type { ScenarioSuiteResponse } from '@/types'

import RegistrySuiteMemberTable from './RegistrySuiteMemberTable'
import { useRegistrySuiteCardStyles } from './RegistrySuiteCard.styles'

interface RegistrySuiteCardProps {
  item: ScenarioSuiteResponse
  members: SuiteMember[]
  sharedWith: (presetName: string) => string[]
  estimatesLoading: boolean
  open: boolean
  busy: boolean
  onOpenChange: (open: boolean) => void
  onAddScenario: (item: ScenarioSuiteResponse) => void
  onEdit: (item: ScenarioSuiteResponse) => void
  onDelete: (item: ScenarioSuiteResponse) => void
  onEditMember: (item: ScenarioSuiteResponse, member: SuiteMember) => void
  onRemoveMember: (item: ScenarioSuiteResponse, member: SuiteMember) => void
}

/** A preset in the Registry, with its scenarios and the actions that change it. */
export default function RegistrySuiteCard({
  item,
  members,
  sharedWith,
  estimatesLoading,
  open,
  busy,
  onOpenChange,
  onAddScenario,
  onEdit,
  onDelete,
  onEditMember,
  onRemoveMember,
}: RegistrySuiteCardProps) {
  const styles = useRegistrySuiteCardStyles()
  const { suite } = item
  const editable = item.origin === 'stored'
  const attention = suiteNeedsAttention(item)
  const meta = [formatScenarioCount(suite.entries.length), suite.author].filter(Boolean).join(' · ')

  return (
    <CollapsibleCard
      title={suite.name}
      meta={meta}
      description={suite.description}
      attention={attention}
      open={open}
      onOpenChange={onOpenChange}
      testId={`registry-suite-${suite.name}`}
      badges={(attention || !editable) && (
        <>
          {attention && (
            <Badge appearance="tint" color="warning" icon={<WarningRegular />}>Needs attention</Badge>
          )}
          {!editable && <Badge appearance="tint" color="informative">Defined in code</Badge>}
        </>
      )}
      actions={editable && (
        <>
          <Button
            className={styles.touchTarget}
            appearance="subtle"
            icon={<AddRegular />}
            disabled={busy}
            aria-label={`Add scenario to ${suite.name}`}
            onClick={() => onAddScenario(item)}
          >
            Add scenario
          </Button>
          <Tooltip relationship="label" content={`Edit ${suite.name}`}>
            <Button
              className={styles.touchTarget}
              appearance="subtle"
              icon={<EditRegular />}
              disabled={busy}
              onClick={() => onEdit(item)}
            />
          </Tooltip>
          <Tooltip relationship="label" content={`Delete ${suite.name}`}>
            <Button
              className={styles.touchTarget}
              appearance="subtle"
              icon={<DeleteRegular />}
              disabled={busy}
              onClick={() => onDelete(item)}
            />
          </Tooltip>
        </>
      )}
    >
      <RegistrySuiteMemberTable
        suiteName={suite.name}
        members={members}
        sharedWith={sharedWith}
        estimatesLoading={estimatesLoading}
        editable={editable}
        busy={busy}
        onEdit={(member) => onEditMember(item, member)}
        onRemove={(member) => onRemoveMember(item, member)}
      />
    </CollapsibleCard>
  )
}
