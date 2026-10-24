import { Tab, TabList } from '@fluentui/react-components'
import { Outlet, useLocation, useNavigate } from 'react-router'

import { useRegistryLayoutStyles } from './Registry.styles'

const REGISTRY_TABS = [
  'targets',
  'converters',
  'scorers',
  'techniques',
  'scenario-presets',
  'scenario-suites',
] as const

/**
 * Keeps a tab selected while its section owns nested create and edit routes.
 */
function selectedTabFromPath(pathname: string): string {
  return (
    REGISTRY_TABS.find(
      (tab) => pathname === `/registry/${tab}` || pathname.startsWith(`/registry/${tab}/`),
    ) ?? 'targets'
  )
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
        <Tab value="scenario-suites">Suites</Tab>
      </TabList>
      <div className={styles.content}>
        <Outlet />
      </div>
    </div>
  )
}
