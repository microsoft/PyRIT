import { type KeyboardEvent, useMemo, useState } from 'react'

import {
  Button,
  Input,
  Link as FluentLink,
  MessageBar,
  MessageBarActions,
  MessageBarBody,
  MessageBarTitle,
  Spinner,
  Text,
} from '@fluentui/react-components'
import { SearchRegular } from '@fluentui/react-icons'
import { useLocation } from 'react-router'

import { useLaunchModuleState } from '@/hooks/useLaunchModuleState'
import { useScenarioCatalog } from '@/hooks/useScenarioCatalog'

import { useScenarioCatalogStyles } from './ScenarioCatalog.styles'
import ScenarioModuleCard from './ScenarioModuleCard'
import { filterModuleGroups, groupScenariosByModule } from './scenarioModules'
import type { ScenarioLaunchNavigationState } from './scenarioRunNavigation'

const SCENARIOS_DOCUMENTATION_URL = 'https://microsoft.github.io/PyRIT/latest/scanner/scanner/'

function matchSummary(count: number, query: string): string {
  return count === 1 ? `1 scenario matches "${query}"` : `${count} scenarios match "${query}"`
}

/** The Scanner's Launch tab: registered scenarios grouped into module cards, each row opening its configure page. */
export default function ScenarioCatalog() {
  const styles = useScenarioCatalogStyles()
  const location = useLocation()
  const { scenarios, modules, loading, estimatesLoading, error, estimateError, retry } = useScenarioCatalog()
  const { openModules, setOpenModules } = useLaunchModuleState()
  const [query, setQuery] = useState('')
  // Cards opened or closed during a search; a new search starts with every matching card open.
  const [searchToggles, setSearchToggles] = useState<ReadonlyMap<string, boolean>>(new Map())

  const searchQuery = query.trim()
  const searching = searchQuery !== ''
  const groups = useMemo(() => groupScenariosByModule(scenarios, modules), [scenarios, modules])
  const results = useMemo(() => filterModuleGroups(groups, searchQuery), [groups, searchQuery])
  const matchCount = results.reduce((count, result) => count + result.matches.length, 0)
  const allOpen = groups.every((group) => openModules.includes(group.key))
  const linkState: ScenarioLaunchNavigationState = { launchSearch: location.search }

  const isOpen = (key: string): boolean => (
    searching ? (searchToggles.get(key) ?? true) : openModules.includes(key)
  )

  const changeQuery = (value: string): void => {
    setQuery(value)
    setSearchToggles(new Map())
  }

  const handleSearchKeyDown = (event: KeyboardEvent<HTMLInputElement>): void => {
    if (event.key === 'Escape' && query) {
      event.preventDefault()
      changeQuery('')
    }
  }

  const changeModuleOpen = (key: string, open: boolean): void => {
    if (searching) {
      setSearchToggles((current) => new Map(current).set(key, open))
    } else {
      setOpenModules(open ? [...openModules, key] : openModules.filter((module) => module !== key))
    }
  }

  const toggleAll = (): void => {
    setOpenModules(allOpen ? [] : groups.map((group) => group.key))
  }

  const hasScenarios = !loading && scenarios.length > 0

  return (
    <section
      className={styles.root}
      data-testid="scenario-catalog"
      aria-labelledby="scenario-catalog-title"
    >
      <div className={styles.header} data-tour="scanner-catalog">
        <div className={styles.headerText}>
          <Text id="scenario-catalog-title" as="h2" size={600} weight="semibold">
            Launch a scan
          </Text>
          <Text size={300} className={styles.subtitle}>
            Choose a scenario to configure.{' '}
            <FluentLink href={SCENARIOS_DOCUMENTATION_URL} target="_blank" rel="noopener noreferrer">
              About scenarios
            </FluentLink>
          </Text>
        </div>
        {hasScenarios && (
          <div className={styles.headerActions}>
            <Input
              className={styles.search}
              contentBefore={<SearchRegular />}
              placeholder="Search modules and scenarios..."
              value={query}
              onChange={(_, data) => changeQuery(data.value)}
              onKeyDown={handleSearchKeyDown}
              aria-label="Search modules and scenarios"
            />
          </div>
        )}
      </div>

      <div role="status" className={styles.visuallyHidden}>
        {hasScenarios && searching ? matchSummary(matchCount, searchQuery) : ''}
      </div>

      {estimateError && (
        <div className={styles.messages}>
          <MessageBar intent="warning">
            <MessageBarBody>{estimateError}</MessageBarBody>
          </MessageBar>
        </div>
      )}

      {error && (
        <div className={styles.messages}>
          <MessageBar intent="error" layout="multiline">
            <MessageBarBody>
              <MessageBarTitle>Couldn&apos;t load scenarios.</MessageBarTitle>
              Try again. If it keeps failing, ask your PyRIT administrator to check the server.
              <Text as="p" size={200} className={styles.errorDetail}>Details: {error}</Text>
            </MessageBarBody>
            <MessageBarActions>
              <Button onClick={retry} className={styles.touchTarget}>Retry</Button>
            </MessageBarActions>
          </MessageBar>
        </div>
      )}

      {loading ? (
        <div className={styles.centeredState}>
          <Spinner label="Loading scenarios..." />
        </div>
      ) : error && scenarios.length === 0 ? null
      : scenarios.length === 0 ? (
        <div className={styles.centeredState} data-testid="empty-state">
          <Text size={400}>No scenarios are registered</Text>
          <Text size={200}>Register a scenario via your PyRIT initializers to see it here.</Text>
        </div>
      ) : results.length === 0 ? (
        <div className={styles.centeredState} data-testid="no-results-state">
          <Text size={400}>No scenarios match &quot;{searchQuery}&quot;</Text>
          <Text size={200}>Try a different search term.</Text>
        </div>
      ) : (
        <>
          {!searching && (
            <div className={styles.listActions}>
              <Button appearance="subtle" size="small" className={styles.touchTarget} onClick={toggleAll}>
                {allOpen ? 'Collapse all' : 'Expand all'}
              </Button>
            </div>
          )}
          <div className={styles.modules}>
            {results.map(({ group, matches }) => (
              <ScenarioModuleCard
                key={group.key}
                group={group}
                scenarios={matches}
                open={isOpen(group.key)}
                onOpenChange={(open) => changeModuleOpen(group.key, open)}
                estimatesLoading={estimatesLoading}
                linkState={linkState}
              />
            ))}
          </div>
        </>
      )}
    </section>
  )
}
