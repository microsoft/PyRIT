import { useState } from 'react'

import {
  Button,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
  MessageBar,
  MessageBarBody,
  MessageBarTitle,
  Spinner,
  Text,
} from '@fluentui/react-components'
import {
  ArrowLeftRegular,
  ArrowRepeatAllRegular,
  ArrowSyncRegular,
  DeleteRegular,
  EditRegular,
  ErrorCircleRegular,
  PlayRegular,
  StopRegular,
} from '@fluentui/react-icons'
import { Link, useNavigate, useParams } from 'react-router'

import { formatTimestamp } from '@/components/AttackResults/attackAttemptFormatting'
import { usePromptGeneration } from '@/hooks/usePromptGeneration'
import { usePromptGenerators } from '@/hooks/usePromptGenerators'
import { promptGenerationsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { PromptGenerationDetail, PromptGeneratorDescriptor } from '@/types'
import {
  datasetDetailPath,
  namedDatasetSelectionKey,
  routerPathParamValue,
  scenarioLaunchRoutePath,
} from '@/utils/routeParams'

import GenerationStageStepper from './GenerationStageStepper'
import { GeneratorReviewView, GeneratorSummaryView } from './generatorRegistry'
import PromptGenerationStatusBadge from './PromptGenerationStatusBadge'
import {
  generationStatusLabel,
  isActiveGenerationStatus,
  isCancellableGenerationStatus,
  isRetryableGenerationStatus,
} from './promptGenerationPresentation'
import { newPromptGenerationRoutePath, PROMPT_GENERATIONS_PATH } from './promptGenerationRoutes'
import { usePromptGenerationStyles } from './PromptGeneration.styles'

type GenerationAction = 'cancel' | 'retry' | 'delete'

const ERROR_TITLES: Partial<Record<PromptGenerationDetail['status'], string>> = {
  failed: 'Generation failed',
  interrupted: 'Generation was interrupted',
  cancelled: 'Generation was cancelled',
}

export default function PromptGenerationPage() {
  const { generationId } = useParams<{ generationId: string }>()
  const decodedId = routerPathParamValue(generationId)
  return <PromptGenerationPageContent key={decodedId} generationId={decodedId} />
}

interface PromptGenerationPageContentProps {
  readonly generationId: string
}

function PromptGenerationPageContent({ generationId }: PromptGenerationPageContentProps) {
  const styles = usePromptGenerationStyles()
  const navigate = useNavigate()
  const { state, retry, applyRecord } = usePromptGeneration(generationId)
  const { generators } = usePromptGenerators()
  const [pending, setPending] = useState<GenerationAction | null>(null)
  const [confirming, setConfirming] = useState<'cancel' | 'delete' | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const backLink = (
    <Link to={PROMPT_GENERATIONS_PATH} className={styles.backLink}>
      <ArrowLeftRegular /> Back to generations
    </Link>
  )

  if (state.record === null) {
    return (
      <section className={styles.root} aria-label="Generation" data-testid="prompt-generation-page">
        <div className={styles.content}>
          {backLink}
          {state.loadStatus === 'loading' ? (
            <div className={styles.centeredState}><Spinner label="Loading generation..." /></div>
          ) : (
            <div className={styles.centeredState}>
              <ErrorCircleRegular fontSize={32} />
              <Text as="h1" size={600} weight="semibold">
                {state.loadStatus === 'not-found' ? 'Generation not found' : 'Unable to load generation'}
              </Text>
              <Text>{state.error}</Text>
              <Button appearance="primary" className={styles.touchTarget} icon={<ArrowSyncRegular />} onClick={retry}>
                Retry
              </Button>
            </div>
          )}
        </div>
      </section>
    )
  }

  const generation = state.record
  const descriptor = generators.find((item) => item.name === generation.generator)

  const runAction = async (action: GenerationAction): Promise<void> => {
    setPending(action)
    setActionError(null)
    try {
      if (action === 'delete') {
        await promptGenerationsApi.remove(generation.id)
        navigate(PROMPT_GENERATIONS_PATH)
        return
      }
      const call = action === 'cancel' ? promptGenerationsApi.cancel : promptGenerationsApi.retry
      applyRecord(await call(generation.id))
      setConfirming(null)
    } catch (error: unknown) {
      setActionError(toApiError(error).detail)
    } finally {
      setPending(null)
    }
  }

  const active = isActiveGenerationStatus(generation.status)
  const errorTitle = ERROR_TITLES[generation.status]

  return (
    <section className={styles.root} aria-labelledby="prompt-generation-title" data-testid="prompt-generation-page">
      <div className={styles.content}>
        {backLink}
        <header className={styles.header}>
          <div className={styles.headerIdentity}>
            <div className={styles.titleRow}>
              <Text id="prompt-generation-title" as="h1" size={700} weight="semibold">{generation.dataset_name}</Text>
              <PromptGenerationStatusBadge status={generation.status} testId="generation-status-badge" />
            </div>
            <Text className={styles.muted}>
              {descriptor?.display_name ?? generation.generator}
              {generation.group_label && ` · Part of ${generation.group_label}`}
            </Text>
          </div>
          <div className={styles.headerActions}>

            {isRetryableGenerationStatus(generation.status) && (
              <Button
                className={styles.touchTarget}
                appearance="primary"
                icon={<ArrowRepeatAllRegular />}
                disabled={pending !== null}
                onClick={() => { void runAction('retry') }}
                data-testid="generation-retry-btn"
              >
                {pending === 'retry' ? 'Retrying...' : 'Retry from last checkpoint'}
              </Button>
            )}
            {isCancellableGenerationStatus(generation.status) && (
              <Button
                className={styles.touchTarget}
                icon={<StopRegular />}
                disabled={pending !== null}
                onClick={() => {
                  setActionError(null)
                  setConfirming('cancel')
                }}
                data-testid="generation-cancel-btn"
              >
                Cancel
              </Button>
            )}
            <Link
              className={styles.secondaryLink}
              to={newPromptGenerationRoutePath({ generator: generation.generator, from: generation.id })}
              data-testid="generation-edit-link"
            >
              <EditRegular aria-hidden /> Edit &amp; regenerate
            </Link>
            {!active && (
              <Button
                className={styles.touchTarget}
                appearance="subtle"
                icon={<DeleteRegular />}
                disabled={pending !== null}
                onClick={() => {
                  setActionError(null)
                  setConfirming('delete')
                }}
                data-testid="generation-delete-btn"
              >
                Delete
              </Button>
            )}
          </div>
        </header>

        {state.stale && state.error && (
          <MessageBar intent="warning">
            <MessageBarBody>Live progress could not be refreshed: {state.error}</MessageBarBody>
          </MessageBar>
        )}
        {actionError && confirming === null && (
          <MessageBar intent="error">
            <MessageBarBody role="alert">{actionError}</MessageBarBody>
          </MessageBar>
        )}
        {generation.error && (
          <MessageBar intent="error" data-testid="generation-error">
            <MessageBarBody>
              <MessageBarTitle>{errorTitle ?? 'Generation reported an error'}</MessageBarTitle>
              <Text className={styles.preformatted}>{generation.error}</Text>
              {isRetryableGenerationStatus(generation.status) && (
                <Text block size={200}>Retry resumes from the last saved checkpoint, so finished stages are not repeated.</Text>
              )}
            </MessageBarBody>
          </MessageBar>
        )}


        <section className={styles.section} aria-labelledby="generation-progress-title">
          <Text id="generation-progress-title" as="h2" size={500} weight="semibold">Progress</Text>
          <GenerationStageStepper descriptor={descriptor} status={generation.status} progress={generation.progress} />
          <span className={styles.liveStatus} aria-live="polite">{generationStatusLabel(generation.status)}</span>
        </section>

        {generation.status === 'awaiting_review' && (
          <GeneratorReviewView generation={generation} descriptor={descriptor} onGenerationChange={applyRecord} />
        )}

        {generation.status === 'completed' && (
          <CompletionPanel generation={generation} descriptor={descriptor} />
        )}

        <dl className={styles.metadata} aria-label="Generation details">
          <MetadataItem label="Created" value={formatTimestamp(generation.created_at)} />
          {generation.started_at && <MetadataItem label="Started" value={formatTimestamp(generation.started_at)} />}
          {generation.completed_at && <MetadataItem label="Completed" value={formatTimestamp(generation.completed_at)} />}
          {generation.created_by && <MetadataItem label="Created by" value={generation.created_by} />}
          <MetadataItem label="Generation ID" value={generation.id} />
        </dl>
      </div>

      <Dialog
        open={confirming !== null}
        onOpenChange={(_, data) => { if (!data.open && pending === null) setConfirming(null) }}
      >
        <DialogSurface>
          <DialogBody>
            <DialogTitle>
              {confirming === 'delete' ? `Delete the generation of ${generation.dataset_name}?` : `Cancel ${generation.dataset_name}?`}
            </DialogTitle>
            <DialogContent className={styles.dialogContent}>
              <Text>
                {confirming === 'delete'
                  ? 'This removes the generation record. A dataset it already saved stays in memory.'
                  : 'Generation stops after the current step. You can retry later from the last checkpoint.'}
              </Text>
              {actionError && (
                <MessageBar intent="error">
                  <MessageBarBody>{actionError}</MessageBarBody>
                </MessageBar>
              )}
            </DialogContent>
            <DialogActions>
              <Button appearance="secondary" disabled={pending !== null} onClick={() => setConfirming(null)}>
                {confirming === 'delete' ? 'Keep it' : 'Keep running'}
              </Button>
              <Button
                appearance="primary"
                disabled={pending !== null}
                onClick={() => { if (confirming) void runAction(confirming) }}
                data-testid="confirm-generation-action"
              >
                {confirming === 'delete' ? 'Delete' : 'Cancel generation'}
              </Button>
            </DialogActions>
          </DialogBody>
        </DialogSurface>
      </Dialog>
    </section>
  )
}

function MetadataItem({ label, value }: { readonly label: string; readonly value: string }) {
  const styles = usePromptGenerationStyles()
  return (
    <div className={styles.metadataItem}>
      <dt className={styles.metadataTerm}>{label}</dt>
      <dd className={styles.metadataValue}>{value}</dd>
    </div>
  )
}

interface CompletionPanelProps {
  readonly generation: PromptGenerationDetail
  readonly descriptor: PromptGeneratorDescriptor | undefined
}

function CompletionPanel({ generation, descriptor }: CompletionPanelProps) {
  const styles = usePromptGenerationStyles()
  const scenario = descriptor?.follow_up_scenario
  return (
    <section className={styles.section} aria-labelledby="generation-complete-title" data-testid="generation-complete">
      <Text id="generation-complete-title" as="h2" size={500} weight="semibold">Dataset ready</Text>
      <Text>The dataset <Text weight="semibold">{generation.dataset_name}</Text> is stored and ready to use.</Text>
      <div className={styles.actions}>
        {scenario && (
          <Link
            className={styles.primaryLink}
            to={scenarioLaunchRoutePath(scenario, [generation.dataset_name])}
            data-testid="generation-run-scenario-link"
          >
            <PlayRegular aria-hidden /> Run {scenario}
          </Link>
        )}
        <Link
          className={scenario ? styles.secondaryLink : styles.primaryLink}
          to={datasetDetailPath(namedDatasetSelectionKey(generation.dataset_name))}
          data-testid="generation-browse-dataset-link"
        >
          Browse dataset
        </Link>
      </div>
      {generation.summary && <GeneratorSummaryView generator={generation.generator} summary={generation.summary} />}
    </section>
  )
}
