import { Tab, TabList } from '@fluentui/react-components'
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
        <Tab value="targets">Targets</Tab>
        <Tab value="converters">Converters</Tab>
        <Tab value="scorers">Scorers</Tab>
        <Tab value="techniques">Techniques</Tab>
        <Tab value="scenario-presets">Scenario presets</Tab>
      </TabList>
      <div className={styles.content}>
        <Outlet />
      </div>
    </div>
  )
}
