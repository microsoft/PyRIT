import { useState } from 'react'
import { Tab, TabList } from '@fluentui/react-components'
import type { SelectTabData, SelectTabEvent } from '@fluentui/react-components'

import type { TargetInstance } from '../../types'
import TargetConfig from './TargetConfig'
import InitializerConfig from './InitializerConfig'
import { useConfigPageStyles } from './ConfigPage.styles'

interface ConfigPageProps {
  activeTarget: TargetInstance | null
  onSetActiveTarget: (target: TargetInstance) => void
}

type ConfigTab = 'targets' | 'initializers'

export default function ConfigPage({ activeTarget, onSetActiveTarget }: ConfigPageProps) {
  const styles = useConfigPageStyles()
  const [selectedTab, setSelectedTab] = useState<ConfigTab>('targets')

  const handleTabSelect = (_: SelectTabEvent, data: SelectTabData): void => {
    setSelectedTab(data.value as ConfigTab)
  }

  return (
    <div className={styles.root} data-testid="config-page">
      <TabList
        selectedValue={selectedTab}
        onTabSelect={handleTabSelect}
        className={styles.tabBar}
      >
        <Tab value="targets" data-testid="config-tab-targets">Targets</Tab>
        <Tab value="initializers" data-testid="config-tab-initializers">Initializers</Tab>
      </TabList>

      <div className={styles.tabPanel}>
        {selectedTab === 'targets' && (
          <TargetConfig activeTarget={activeTarget} onSetActiveTarget={onSetActiveTarget} />
        )}
        {selectedTab === 'initializers' && <InitializerConfig />}
      </div>
    </div>
  )
}
