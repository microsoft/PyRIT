import { Button, Tab, TabList, Text } from '@fluentui/react-components'
import type { SelectTabData, SelectTabEvent } from '@fluentui/react-components'
import { AddRegular } from '@fluentui/react-icons'

import { useFeatureNavigationStyles } from './FeatureNavigation.styles'

interface FeatureNavigationProps {
  readonly feature: 'attacks' | 'scanner'
  readonly selectedTab: 'chat' | 'launch' | 'history'
  readonly onTabChange: (tab: 'chat' | 'launch' | 'history') => void
  readonly onNewAttack: () => void
}

export default function FeatureNavigation({ feature, selectedTab, onTabChange, onNewAttack }: FeatureNavigationProps) {
  const styles = useFeatureNavigationStyles()
  return (
    <header className={styles.root} data-tour="feature-tabs">
      <div className={styles.tabs}>
        <Text as="h1" size={500} weight="semibold">{feature === 'attacks' ? 'Attacks' : 'Scanner'}</Text>
        <TabList
          aria-label={feature === 'attacks' ? 'Attack views' : 'Scanner views'}
          selectedValue={selectedTab}
          onTabSelect={(_: SelectTabEvent, data: SelectTabData) => {
            if (data.value === 'chat' || data.value === 'launch' || data.value === 'history') {
              onTabChange(data.value)
            }
          }}
        >
          <Tab value={feature === 'attacks' ? 'chat' : 'launch'}>{feature === 'attacks' ? 'Chat' : 'Launch'}</Tab>
          <Tab value="history">History</Tab>
        </TabList>
      </div>
      {feature === 'attacks' && (
        <Button className={styles.action} appearance="primary" icon={<AddRegular />} onClick={onNewAttack}>New attack</Button>
      )}
    </header>
  )
}
