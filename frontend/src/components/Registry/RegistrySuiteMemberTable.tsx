import {
  Button,
  mergeClasses,
  Spinner,
  Table,
  TableBody,
  TableCell,
  TableHeader,
  TableHeaderCell,
  TableRow,
  Text,
} from '@fluentui/react-components'
import { EditRegular, SubtractCircleRegular } from '@fluentui/react-icons'

import { ScenarioRunEstimateSummary } from '@/components/Scenarios/ScenarioRunEstimate'
import { mapScenarioRunEstimate } from '@/components/Scenarios/scenarioRunEstimateAdapter'
import {
  memberDescription,
  memberScenarioName,
  memberTechniqueNames,
  type SuiteMember,
} from '@/components/ScenarioSuites/suiteMembers'

import { useRegistrySuiteMemberTableStyles } from './RegistrySuiteMemberTable.styles'

interface RegistrySuiteMemberTableProps {
  suiteName: string
  members: SuiteMember[]
  /** Names of the other presets that use each stored scenario, by its stored name. */
  sharedWith: (presetName: string) => string[]
  estimatesLoading: boolean
  /** False for a preset defined in code, which lists its scenarios without actions. */
  editable: boolean
  busy: boolean
  onEdit: (member: SuiteMember) => void
  onRemove: (member: SuiteMember) => void
}

function fixHint(member: SuiteMember): string {
  return member.preset === undefined
    ? 'Remove it from this preset.'
    : 'Remove it, or edit it to use a registered scenario.'
}

function canEdit(member: SuiteMember): boolean {
  return member.preset?.origin === 'stored'
}

/** The scenarios in one Registry preset, each with its own Edit and Remove. */
export default function RegistrySuiteMemberTable({
  suiteName,
  members,
  sharedWith,
  estimatesLoading,
  editable,
  busy,
  onEdit,
  onRemove,
}: RegistrySuiteMemberTableProps) {
  const styles = useRegistrySuiteMemberTableStyles()

  if (members.length === 0) {
    return <Text size={200}>This preset has no scenarios yet. Add one to launch it from the Scanner.</Text>
  }

  return (
    <div className={styles.container}>
      <Table className={styles.table} size="small" aria-label={`Scenarios in ${suiteName}`}>
        <TableHeader className={styles.header}>
          <TableRow>
            <TableHeaderCell className={styles.scenarioColumn}>Scenario</TableHeaderCell>
            <TableHeaderCell>Techniques</TableHeaderCell>
            <TableHeaderCell>Run size</TableHeaderCell>
            {editable && <TableHeaderCell>Actions</TableHeaderCell>}
          </TableRow>
        </TableHeader>
        <TableBody className={styles.body}>
          {members.map((member) => {
            const issues = member.status.issues
            const description = memberDescription(member)
            const others = sharedWith(member.presetName)
            const techniques = memberTechniqueNames(member)
            const runSize = member.preset?.run_size ?? null
            return (
              <TableRow
                key={member.entryId}
                className={mergeClasses(styles.row, issues.length > 0 && styles.brokenRow)}
                data-testid={`suite-member-${member.entryId}`}
              >
                <TableCell className={styles.cell}>
                  <div className={styles.stack}>
                    <Text weight="semibold">{memberScenarioName(member)}</Text>
                    <Text size={200} className={styles.secondary}>Saved as {member.presetName}</Text>
                    {description && <Text size={200}>{description}</Text>}
                    {others.length > 0 && (
                      <Text size={200} className={styles.secondary}>
                        Also in {others.length} other preset{others.length === 1 ? '' : 's'}
                      </Text>
                    )}
                    {issues.map((issue) => (
                      <Text key={`${issue.field}:${issue.message}`} size={200} className={styles.issue}>
                        {issue.message}
                      </Text>
                    ))}
                    {issues.length > 0 && <Text size={200} className={styles.issue}>{fixHint(member)}</Text>}
                  </div>
                </TableCell>
                <TableCell className={styles.cell}>
                  <div className={styles.stack}>
                    <Text>{techniques.length} technique{techniques.length === 1 ? '' : 's'}</Text>
                    {techniques.length > 0 && (
                      <Text size={200} className={styles.secondary}>{techniques.join(' · ')}</Text>
                    )}
                  </div>
                </TableCell>
                <TableCell className={styles.cell}>
                  {estimatesLoading ? (
                    <Spinner size="tiny" label="Calculating..." labelPosition="after" />
                  ) : runSize ? (
                    <ScenarioRunEstimateSummary state={mapScenarioRunEstimate(runSize, 'request')} compact />
                  ) : (
                    <Text size={200} className={styles.secondary}>—</Text>
                  )}
                </TableCell>
                {editable && (
                  <TableCell className={styles.cell}>
                    <div className={styles.actions}>
                      {canEdit(member) && (
                        <Button
                          className={styles.touchTarget}
                          appearance="subtle"
                          icon={<EditRegular />}
                          disabled={busy}
                          aria-label={`Edit ${member.presetName}`}
                          onClick={() => onEdit(member)}
                        >
                          Edit
                        </Button>
                      )}
                      <Button
                        className={styles.touchTarget}
                        appearance="subtle"
                        icon={<SubtractCircleRegular />}
                        disabled={busy}
                        aria-label={`Remove ${member.presetName} from ${suiteName}`}
                        onClick={() => onRemove(member)}
                      >
                        Remove
                      </Button>
                    </div>
                  </TableCell>
                )}
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}
