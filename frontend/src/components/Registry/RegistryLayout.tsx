import { Tab, TabList, Tooltip } from '@fluentui/react-components'
import { Outlet, useLocation, useNavigate } from 'react-router'

import { useRegistryLayoutStyles } from './Registry.styles'

const REGISTRY_TABS = [
  'targets',
  'converters',
  'scorers',
  'techniques',
  'scenario-presets',
] as const

/** Suites are defined on the Scenario presets page, so their editor routes keep that tab selected. */
const SUITE_SECTION = 'scenario-suites'

function matchesSection(pathname: string, section: string): boolean {
  return pathname === `/registry/${section}` || pathname.startsWith(`/registry/${section}/`)
}

/**
 * Keeps a tab selected while its section owns nested create and edit routes.
 */
function selectedTabFromPath(pathname: string): string {
  if (matchesSection(pathname, SUITE_SECTION)) {
    return 'scenario-presets'
  }
  return REGISTRY_TABS.find((tab) => matchesSection(pathname, tab)) ?? 'targets'
}

export default function RegistryLayout() {
  const styles = useRegistryLayoutStyles()
  const location = useLocation()
  const navigate = useNavigate()
  const selectedTab = selectedTabFromPath(location.pathname)

  return (
    <div className={styles.root}>
      <TabList
        className={styles.tabs}
        data-tour="registry-tabs"
        selectedValue={selectedTab}
        onTabSelect={(_, data) => navigate(`/registry/${String(data.value)}`)}
        aria-label="Registry sections"
      >
        <Tooltip relationship="description" content="Model or endpoint connections that send prompts and return responses.">
          <Tab value="targets">Targets</Tab>
        </Tooltip>
        <Tooltip relationship="description" content="Reusable transformations for prompts or responses, such as text encoding or translation.">
          <Tab value="converters">Converters</Tab>
        </Tooltip>
        <Tooltip relationship="description" content="Components that evaluate evidence and return a score or verdict.">
          <Tab value="scorers">Scorers</Tab>
        </Tooltip>
        <Tooltip relationship="description" content="Named attack setups that combine an algorithm with prompts, converters, and settings.">
          <Tab value="techniques">Techniques</Tab>
        </Tooltip>
        <Tooltip relationship="description" content="Reusable scenario settings. Choose the target and run-specific settings when you launch.">
          <Tab value="scenario-presets">Scenario presets</Tab>
        </Tooltip>
      </TabList>
      <div className={styles.content}>
        <Outlet />
      </div>
    </div>
  )
}
