import { Tab, TabList } from '@fluentui/react-components'
import { Outlet, useLocation, useNavigate } from 'react-router'

import { useRegistryLayoutStyles } from './Registry.styles'

export default function RegistryLayout() {
  const styles = useRegistryLayoutStyles()
  const location = useLocation()
  const navigate = useNavigate()
  const selectedTab = location.pathname.endsWith('/techniques') ? 'techniques'
    : location.pathname.endsWith('/converters') ? 'converters' : 'targets'

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
        <Tab value="techniques">Techniques</Tab>
      </TabList>
      <div className={styles.content}>
        <Outlet />
      </div>
    </div>
  )
}
