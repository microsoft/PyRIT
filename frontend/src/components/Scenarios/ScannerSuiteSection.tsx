import { useId, useState } from 'react'

import {
  Badge,
  Button,
  mergeClasses,
  Table,
  TableBody,
  TableCell,
  TableHeader,
  TableHeaderCell,
  TableRow,
  Text,
} from '@fluentui/react-components'
import { ChevronDownRegular, ChevronUpRegular, PlayRegular } from '@fluentui/react-icons'

import LaunchSuiteDialog from '@/components/ScenarioSuites/LaunchSuiteDialog'
import type { ScenarioPresetResponse, ScenarioSuiteResponse, TargetInstance } from '@/types'

import { useScannerSuiteSectionStyles } from './ScannerSuiteSection.styles'
import { SCANNER_CELL_PADDING_CLASS, useScenarioCatalogStyles } from './ScenarioCatalog.styles'

interface ScannerSuiteSectionProps {
  suites: ScenarioSuiteResponse[]
  /** Every preset by name, including presets hidden from the Scanner's own list. */
  presets: ReadonlyMap<string, ScenarioPresetResponse>
  targets: TargetInstance[]
  defaultObjectiveTarget: TargetInstance | null
  defaultAdversarialTarget: TargetInstance | null
  labels: Record<string, string>
  /** The saved Operation that every child run joins, or null for none. */
  operationId: string | null
  /** False while server defaults or the global Operation selection are unresolved. */
  defaultsReady: boolean
  onSuiteChanged: (name: string) => void
}

interface ScannerSuiteRowProps {
  item: ScenarioSuiteResponse
  presets: ReadonlyMap<string, ScenarioPresetResponse>
  defaultsReady: boolean
  onLaunch: (item: ScenarioSuiteResponse) => void
}

interface SuiteIssue {
  key: string
  message: string
}

function suiteIssues(item: ScenarioSuiteResponse): SuiteIssue[] {
  return item.entry_statuses.flatMap((status, index) => status.issues.map((issue) => ({
    key: `${status.entry_id}:${issue.field}:${issue.message}`,
    message: `Entry ${index + 1} (${status.preset_name}): ${issue.message}`,
  })))
}

function ScannerSuiteRow({ item, presets, defaultsReady, onLaunch }: ScannerSuiteRowProps) {
  const catalogStyles = useScenarioCatalogStyles()
  const styles = useScannerSuiteSectionStyles()
  const membersId = useId()
  const [expanded, setExpanded] = useState(false)
  const { suite } = item
  const entryCount = suite.entries.length
  const issues = suiteIssues(item)
  const launchable = entryCount > 0 && issues.length === 0 && defaultsReady
  const cellClassName = mergeClasses(
    catalogStyles.tableCell,
    catalogStyles.tableCellPadding,
    SCANNER_CELL_PADDING_CLASS,
  )

  return (
    <TableRow className={catalogStyles.summaryRow} data-testid={`suite-card-${suite.name}`}>
      <TableCell className={cellClassName}>
        <Text className={catalogStyles.mobileLabel} size={200} weight="semibold">
          Suite / purpose
        </Text>
        <div className={catalogStyles.scenarioSummary}>
          <div className={styles.titleRow}>
            <Text weight="semibold">{suite.name}</Text>
            {issues.length > 0 && (
              <Badge appearance="tint" color="warning">
                Needs attention
              </Badge>
            )}
          </div>
          {suite.description && (
            <Text size={200} className={catalogStyles.secondaryText}>
              {suite.description}
            </Text>
          )}
          {issues.length > 0 && (
            <ul className={catalogStyles.issueList} data-testid={`suite-issues-${suite.name}`}>
              {issues.map((issue) => (
                <li key={issue.key}>
                  <Text size={200}>{issue.message}</Text>
                </li>
              ))}
            </ul>
          )}
        </div>
      </TableCell>
      <TableCell className={cellClassName}>
        <Text className={catalogStyles.mobileLabel} size={200} weight="semibold">
          Scenarios
        </Text>
        <div className={catalogStyles.compactStack}>
          <div className={styles.memberSummary}>
            <Text weight="semibold">
              {entryCount} scenario{entryCount === 1 ? '' : 's'}
            </Text>
            {entryCount > 0 && (
              <Button
                appearance="transparent"
                size="small"
                className={mergeClasses(catalogStyles.descriptionToggle, catalogStyles.touchTarget)}
                icon={expanded ? <ChevronUpRegular /> : <ChevronDownRegular />}
                aria-controls={membersId}
                aria-expanded={expanded}
                aria-label={`${expanded ? 'Hide' : 'Show'} scenarios in ${suite.name}`}
                onClick={() => setExpanded((current) => !current)}
              />
            )}
          </div>
          {entryCount > 0 && (
            <ol
              id={membersId}
              hidden={!expanded}
              className={styles.memberList}
              aria-label={`Scenarios in ${suite.name}`}
            >
              {suite.entries.map((entry) => {
                const scenarioName = presets.get(entry.preset_name)?.preset.scenario_name
                return (
                  <li key={entry.entry_id}>
                    <Text size={200} weight="semibold">{entry.preset_name}</Text>
                    {scenarioName && (
                      <Text size={200} className={catalogStyles.secondaryText}>
                        {` · Runs ${scenarioName}`}
                      </Text>
                    )}
                  </li>
                )
              })}
            </ol>
          )}
        </div>
      </TableCell>
      <TableCell className={cellClassName}>
        <Text className={catalogStyles.mobileLabel} size={200} weight="semibold">
          Actions
        </Text>
        <div>
          <Button
            className={catalogStyles.touchTarget}
            appearance="primary"
            icon={<PlayRegular />}
            disabled={!launchable}
            onClick={() => onLaunch(item)}
            data-testid={`launch-suite-${suite.name}`}
          >
            Launch
          </Button>
        </div>
      </TableCell>
    </TableRow>
  )
}

/** Suites run all their presets as one launch, so they lead the Scanner ahead of single scenarios. */
export default function ScannerSuiteSection({
  suites,
  presets,
  targets,
  defaultObjectiveTarget,
  defaultAdversarialTarget,
  labels,
  operationId,
  defaultsReady,
  onSuiteChanged,
}: ScannerSuiteSectionProps) {
  const catalogStyles = useScenarioCatalogStyles()
  const styles = useScannerSuiteSectionStyles()
  const titleId = useId()
  const [launching, setLaunching] = useState<ScenarioSuiteResponse | null>(null)
  const headerCellClassName = (columnClassName: string): string => mergeClasses(
    columnClassName,
    catalogStyles.tableHeaderCell,
    SCANNER_CELL_PADDING_CLASS,
  )

  return (
    <section
      className={catalogStyles.section}
      data-testid="scanner-suites"
      aria-labelledby={titleId}
    >
      <Text id={titleId} as="h2" size={500} weight="semibold">
        Scenario suites
      </Text>
      <div className={catalogStyles.tableContainer}>
        <Table className={catalogStyles.table} size="small" aria-label="Scenario suites">
          <TableHeader className={catalogStyles.tableHeader}>
            <TableRow>
              <TableHeaderCell className={headerCellClassName(styles.suiteColumn)}>
                Suite / purpose
              </TableHeaderCell>
              <TableHeaderCell className={headerCellClassName(styles.scenariosColumn)}>
                Scenarios
              </TableHeaderCell>
              <TableHeaderCell className={headerCellClassName(styles.actionsColumn)}>
                Actions
              </TableHeaderCell>
            </TableRow>
          </TableHeader>
          <TableBody className={catalogStyles.tableBody}>
            {suites.map((item) => (
              <ScannerSuiteRow
                key={item.suite.name}
                item={item}
                presets={presets}
                defaultsReady={defaultsReady}
                onLaunch={setLaunching}
              />
            ))}
          </TableBody>
        </Table>
      </div>

      {launching && (
        <LaunchSuiteDialog
          suite={launching}
          targets={targets}
          defaultObjectiveTarget={defaultObjectiveTarget}
          defaultAdversarialTarget={defaultAdversarialTarget}
          labels={labels}
          operationId={operationId}
          defaultsReady={defaultsReady}
          onDismiss={() => setLaunching(null)}
          onSuiteChanged={() => {
            const changedName = launching.suite.name
            setLaunching(null)
            onSuiteChanged(changedName)
          }}
        />
      )}
    </section>
  )
}
