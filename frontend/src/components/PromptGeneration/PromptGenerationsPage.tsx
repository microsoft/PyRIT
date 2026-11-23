import { Fragment, useId, useState } from 'react'

import {
  Button,
  Menu,
  MenuItem,
  MenuList,
  MenuPopover,
  MenuTrigger,
  MessageBar,
  MessageBarBody,
  Spinner,
  Table,
  TableBody,
  TableCell,
  TableHeader,
  TableHeaderCell,
  TableRow,
  Text,
} from '@fluentui/react-components'
import { AddRegular, ArrowLeftRegular, ArrowSyncRegular, PlayRegular, StopRegular } from '@fluentui/react-icons'
import { Link, useNavigate } from 'react-router'

import { formatTimestamp } from '@/components/AttackResults/attackAttemptFormatting'
import { usePromptGenerationList } from '@/hooks/usePromptGeneration'
import { usePromptGenerators } from '@/hooks/usePromptGenerators'
import { promptGenerationsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { PromptGenerationSummary, PromptGeneratorDescriptor } from '@/types'
import { DATASETS_PATH, scenarioLaunchRoutePath } from '@/utils/routeParams'

import PromptGenerationStatusBadge from './PromptGenerationStatusBadge'
import {
  aggregateGenerationStatus,
  countGenerationStatuses,
  generationListRows,
  generatorDisplayName,
  isCancellableGenerationStatus,
  progressText,
} from './promptGenerationPresentation'
import { newPromptGenerationRoutePath, promptGenerationRoutePath } from './promptGenerationRoutes'
import { usePromptGenerationStyles } from './PromptGeneration.styles'

interface GenerationRowProps {
  readonly generation: PromptGenerationSummary
  readonly generators: readonly PromptGeneratorDescriptor[]
  readonly nested?: boolean
  readonly cancelling: boolean
  readonly onCancel: (generation: PromptGenerationSummary) => void
}

function GenerationRow({ generation, generators, nested = false, cancelling, onCancel }: GenerationRowProps) {
  const styles = usePromptGenerationStyles()
  const descriptor = generators.find((item) => item.name === generation.generator)
  return (
    <TableRow data-testid={`generation-row-${generation.id}`}>
      <TableCell className={nested ? styles.nestedCell : undefined}>
        <div className={styles.cellStack}>
          <Link className={styles.inlineLink} to={promptGenerationRoutePath(generation.id)}>
            {generation.dataset_name}
          </Link>
          {generation.error && <Text size={200} className={styles.errorText}>{generation.error}</Text>}
        </div>
      </TableCell>
      <TableCell>{generatorDisplayName(generators, generation.generator)}</TableCell>
      <TableCell><PromptGenerationStatusBadge status={generation.status} appearance="tint" /></TableCell>
      <TableCell>{progressText(generation.status, generation.progress, descriptor)}</TableCell>
      <TableCell>{formatTimestamp(generation.created_at)}</TableCell>
      <TableCell>
        {isCancellableGenerationStatus(generation.status) && (
          <Button
            className={styles.touchTarget}
            appearance="subtle"
            icon={<StopRegular />}
            disabled={cancelling}
            aria-label={`Cancel ${generation.dataset_name}`}
            onClick={() => onCancel(generation)}
            data-testid={`generation-row-cancel-${generation.id}`}
          >
            Cancel
          </Button>
        )}
      </TableCell>
    </TableRow>
  )
}

/** Generations newest first, with batch members grouped under their group label. */
export default function PromptGenerationsPage() {
  const styles = usePromptGenerationStyles()
  const titleId = useId()
  const navigate = useNavigate()
  const { generators, loading: generatorsLoading, error: generatorsError, retry: retryGenerators } = usePromptGenerators()
  const { state, retry } = usePromptGenerationList()
  const [cancellingId, setCancellingId] = useState<string | null>(null)
  const [cancelError, setCancelError] = useState<string | null>(null)
  const generations = state.record ?? []
  const rows = generationListRows(generations)

  const handleCancel = async (generation: PromptGenerationSummary): Promise<void> => {
    setCancellingId(generation.id)
    setCancelError(null)
    try {
      await promptGenerationsApi.cancel(generation.id)
      retry()
    } catch (error: unknown) {
      setCancelError(`Could not cancel ${generation.dataset_name}: ${toApiError(error).detail}`)
    } finally {
      setCancellingId(null)
    }
  }
  const rowActions = {
    cancelling: cancellingId !== null,
    onCancel: (generation: PromptGenerationSummary) => { void handleCancel(generation) },
  }

  return (
    <section className={styles.root} aria-labelledby={titleId} data-testid="prompt-generations-page">
      <div className={styles.content}>
        <Link to={DATASETS_PATH} className={styles.backLink}>
          <ArrowLeftRegular /> Back to datasets
        </Link>
        <header className={styles.header}>
          <div className={styles.headerIdentity}>
            <Text id={titleId} as="h1" size={600} weight="semibold">Generate datasets</Text>
            <Text className={styles.subtitle}>
              Generators write seed datasets from a policy or instructions. Each generation runs in the background
              and saves a stored dataset you can browse or run in a scan.
            </Text>
          </div>
          <div className={styles.headerActions}>
            <Menu>
              <MenuTrigger disableButtonEnhancement>
                <Button
                  className={styles.touchTarget}
                  appearance="primary"
                  icon={<AddRegular />}
                  disabled={generatorsLoading || generators.length === 0}
                  data-testid="new-generation-btn"
                >
                  New generation
                </Button>
              </MenuTrigger>
              <MenuPopover>
                <MenuList>
                  {generators.map((generator) => (
                    <MenuItem
                      key={generator.name}
                      onClick={() => navigate(newPromptGenerationRoutePath({ generator: generator.name }))}
                    >
                      {generator.display_name}
                    </MenuItem>
                  ))}
                </MenuList>
              </MenuPopover>
            </Menu>
          </div>
        </header>

        {generatorsError && (
          <MessageBar intent="error">
            <MessageBarBody>
              Generators could not be loaded: {generatorsError}
              <Button className={styles.touchTarget} icon={<ArrowSyncRegular />} onClick={retryGenerators}>Retry</Button>
            </MessageBarBody>
          </MessageBar>
        )}
        {state.error && (
          <MessageBar intent={state.stale ? 'warning' : 'error'}>
            <MessageBarBody>
              {state.stale ? 'The list could not be refreshed: ' : 'Generations could not be loaded: '}
              {state.error}
              <Button className={styles.touchTarget} icon={<ArrowSyncRegular />} onClick={retry}>Retry</Button>
            </MessageBarBody>
          </MessageBar>
        )}

        {cancelError && (
          <MessageBar intent="error">
            <MessageBarBody role="alert">{cancelError}</MessageBarBody>
          </MessageBar>
        )}

        {state.loadStatus === 'loading' ? (
          <div className={styles.centeredState}><Spinner label="Loading generations..." /></div>
        ) : state.record !== null && generations.length === 0 ? (
          <div className={styles.centeredState} data-testid="generations-empty">
            <Text size={400}>No generations yet</Text>
            <Text className={styles.muted}>Choose New generation to write a dataset from a policy or instructions.</Text>
          </div>
        ) : generations.length > 0 && (
          <div className={styles.tableScroll}>
            <Table className={styles.table} aria-label="Generations">
              <TableHeader>
                <TableRow>
                  <TableHeaderCell>Dataset</TableHeaderCell>
                  <TableHeaderCell>Generator</TableHeaderCell>
                  <TableHeaderCell>Status</TableHeaderCell>
                  <TableHeaderCell>Progress</TableHeaderCell>
                  <TableHeaderCell>Created</TableHeaderCell>
                  <TableHeaderCell>Actions</TableHeaderCell>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((row) => {
                  if (row.kind === 'generation') {
                    return <GenerationRow key={row.generation.id} generation={row.generation} generators={generators} {...rowActions} />
                  }
                  const counts = countGenerationStatuses(row.items)
                  const followUpScenario = generators.find((item) => item.name === row.items[0]?.generator)?.follow_up_scenario
                  const allCompleted = counts.total > 0 && counts.completed === counts.total
                  return (
                    <Fragment key={row.groupId}>
                      <TableRow className={styles.groupRow} data-testid={`generation-group-row-${row.groupId}`}>
                        <TableCell><Text weight="semibold">{row.label}</Text></TableCell>
                        <TableCell>{generatorDisplayName(generators, row.items[0]?.generator ?? '')}</TableCell>
                        <TableCell>
                          <PromptGenerationStatusBadge status={aggregateGenerationStatus(row.items)} appearance="tint" />
                        </TableCell>
                        <TableCell>{counts.completed} of {counts.total} datasets completed</TableCell>
                        <TableCell />
                        <TableCell>
                          {allCompleted && followUpScenario && (
                            <Link
                              className={styles.inlineLink}
                              to={scenarioLaunchRoutePath(followUpScenario, row.items.map((item) => item.dataset_name))}
                              data-testid="generation-group-run-scenario-link"
                            >
                              <PlayRegular aria-hidden /> Run {followUpScenario} with these {counts.total} datasets
                            </Link>
                          )}
                        </TableCell>
                      </TableRow>
                      {row.items.map((item) => (
                        <GenerationRow key={item.id} generation={item} generators={generators} nested {...rowActions} />
                      ))}
                    </Fragment>
                  )
                })}
              </TableBody>
            </Table>
          </div>
        )}
      </div>
    </section>
  )
}
