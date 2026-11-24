import { useMemo, useState } from 'react'

import {
  Button,
  Input,
  MessageBar,
  MessageBarActions,
  MessageBarBody,
  Spinner,
  Text,
} from '@fluentui/react-components'
import { AddRegular, DismissRegular, SearchRegular, SettingsRegular } from '@fluentui/react-icons'
import { useNavigate } from 'react-router'

import { NEW_PRESET_ROUTE, PRESETS_ROUTE } from '@/components/ScenarioPresets/presetRoutes'
import LaunchSuiteDialog from '@/components/ScenarioSuites/LaunchSuiteDialog'
import SuiteCard from '@/components/ScenarioSuites/SuiteCard'
import {
  memberScenarioName,
  suiteMembers,
  suiteNeedsAttention,
} from '@/components/ScenarioSuites/suiteMembers'
import { useScenarioSuiteLibrary } from '@/hooks/useScenarioSuiteLibrary'
import type { ScenarioSuiteResponse, TargetInstance } from '@/types'

import { useScenarioCatalogStyles } from './ScenarioCatalog.styles'
import { useScannerPresetsPageStyles } from './ScannerPresetsPage.styles'

const DEFAULTS_LOADING_REASON = 'Launch is available once default labels and the Operation finish loading.'
const NO_SCENARIOS_REASON = 'Add a scenario to this preset in the Registry before launching it.'

interface ScannerPresetsPageProps {
  targets: TargetInstance[]
  defaultObjectiveTarget: TargetInstance | null
  defaultAdversarialTarget: TargetInstance | null
  labels: Record<string, string>
  /** The saved Operation that every scenario run joins, or null for none. */
  operationId: string | null
  /** False while server defaults or the global Operation selection are unresolved. */
  defaultsReady: boolean
}

function launchBlockedReason(item: ScenarioSuiteResponse, defaultsReady: boolean): string | null {
  if (item.suite.entries.length === 0) {
    return NO_SCENARIOS_REASON
  }
  return defaultsReady ? null : DEFAULTS_LOADING_REASON
}

/** The Scanner's Presets tab: saved sets of scenarios that launch together. */
export default function ScannerPresetsPage({
  targets,
  defaultObjectiveTarget,
  defaultAdversarialTarget,
  labels,
  operationId,
  defaultsReady,
}: ScannerPresetsPageProps) {
  const catalogStyles = useScenarioCatalogStyles()
  const styles = useScannerPresetsPageStyles()
  const navigate = useNavigate()
  const library = useScenarioSuiteLibrary()
  const [query, setQuery] = useState('')
  const [launching, setLaunching] = useState<ScenarioSuiteResponse | null>(null)
  const [changedNotice, setChangedNotice] = useState<string | null>(null)

  // Presets that need attention cannot launch here; the Registry lists and fixes them.
  const launchable = useMemo(
    () => library.suites
      .filter((item) => !suiteNeedsAttention(item))
      .map((item) => ({ item, members: suiteMembers(item, library.presets, library.scenarios) }))
      .sort((left, right) => left.item.suite.name.localeCompare(right.item.suite.name)),
    [library.suites, library.presets, library.scenarios],
  )
  const normalizedQuery = query.trim().toLowerCase()
  const visible = launchable.filter(({ item, members }) => normalizedQuery === '' || [
    item.suite.name,
    item.suite.description ?? '',
    ...members.map(memberScenarioName),
  ].join(' ').toLowerCase().includes(normalizedQuery))

  const handleSuiteChanged = (name: string): void => {
    setLaunching(null)
    library.reload()
    setChangedNotice(`'${name}' or one of its scenarios changed while you were launching it. Review it and launch again.`)
  }

  const hasPresets = launchable.length > 0

  return (
    <section className={catalogStyles.root} data-testid="scanner-presets" aria-labelledby="scanner-presets-title">
      <div className={catalogStyles.header}>
        <div className={catalogStyles.headerText}>
          <Text id="scanner-presets-title" as="h2" size={600} weight="semibold">Presets</Text>
          <Text size={300} className={catalogStyles.subtitle}>
            Saved sets of scenarios you can launch together.
          </Text>
        </div>
        {hasPresets && (
          <div className={catalogStyles.headerActions}>
            <Input
              className={catalogStyles.search}
              contentBefore={<SearchRegular />}
              placeholder="Search presets..."
              value={query}
              onChange={(_, data) => setQuery(data.value)}
              aria-label="Search presets"
            />
            <Button
              className={styles.touchTarget}
              appearance="subtle"
              icon={<SettingsRegular />}
              onClick={() => navigate(PRESETS_ROUTE)}
            >
              Manage in Registry
            </Button>
          </div>
        )}
      </div>

      {(changedNotice || library.estimateError) && (
        <div className={catalogStyles.messages}>
          {changedNotice && (
            <MessageBar intent="warning" data-testid="suite-changed-notice">
              <MessageBarBody>{changedNotice}</MessageBarBody>
              <MessageBarActions>
                <Button
                  appearance="subtle"
                  className={styles.touchTarget}
                  icon={<DismissRegular />}
                  aria-label="Dismiss notification"
                  onClick={() => setChangedNotice(null)}
                />
              </MessageBarActions>
            </MessageBar>
          )}
          {library.estimateError && (
            <MessageBar intent="warning">
              <MessageBarBody>{library.estimateError}</MessageBarBody>
            </MessageBar>
          )}
        </div>
      )}

      {library.error && (
        <MessageBar intent="error">
          <MessageBarBody>Couldn&apos;t load presets. {library.error}</MessageBarBody>
          <MessageBarActions>
            <Button className={styles.touchTarget} onClick={library.reload}>Retry</Button>
          </MessageBarActions>
        </MessageBar>
      )}
      {library.loading ? (
        <div className={catalogStyles.centeredState}>
          <Spinner label="Loading presets..." />
        </div>
      ) : library.error && !hasPresets ? null
      : !hasPresets ? (
        <div className={catalogStyles.centeredState} data-testid="presets-empty-state">
          <Text as="h3" size={400} weight="semibold">No presets yet</Text>
          <Text size={300}>
            A preset saves scenarios with their techniques, datasets, and limits so you can launch them again in one
            click.
          </Text>
          <Button
            className={styles.touchTarget}
            appearance="primary"
            icon={<AddRegular />}
            onClick={() => navigate(NEW_PRESET_ROUTE)}
          >
            Create a preset
          </Button>
          <Text size={200} className={styles.emptyHint}>Presets are managed in the Registry.</Text>
        </div>
      ) : visible.length === 0 ? (
        <div className={catalogStyles.centeredState} data-testid="no-results-state">
          <Text size={400}>No presets match &quot;{query}&quot;</Text>
          <Text size={200}>Try a different search term.</Text>
        </div>
      ) : (
        <div className={styles.cards}>
          {visible.map(({ item, members }) => (
            <SuiteCard
              key={item.suite.name}
              item={item}
              members={members}
              estimatesLoading={library.estimatesLoading}
              launchBlockedReason={launchBlockedReason(item, defaultsReady)}
              onLaunch={setLaunching}
            />
          ))}
        </div>
      )}

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
          onSuiteChanged={() => handleSuiteChanged(launching.suite.name)}
        />
      )}
    </section>
  )
}
