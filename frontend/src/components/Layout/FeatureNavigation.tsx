import { Button, Tab, TabList, Text } from '@fluentui/react-components'
import type { SelectTabData, SelectTabEvent } from '@fluentui/react-components'
import { AddRegular } from '@fluentui/react-icons'

import { useFeatureNavigationStyles } from './FeatureNavigation.styles'

type AttackTab = 'chat' | 'history'
export type ScannerTab = 'launch' | 'presets' | 'history'
type FeatureTab = AttackTab | ScannerTab

interface FeatureTabOption {
  readonly value: FeatureTab
  readonly label: string
}

const ATTACK_TABS: readonly FeatureTabOption[] = [
  { value: 'chat', label: 'Chat' },
  { value: 'history', label: 'History' },
]

const SCANNER_TABS: readonly FeatureTabOption[] = [
  { value: 'launch', label: 'Launch' },
  { value: 'presets', label: 'Presets' },
  { value: 'history', label: 'History' },
]

interface FeatureNavigationProps {
  readonly feature: 'attacks' | 'scanner'
  readonly selectedTab: FeatureTab
  readonly onTabChange: (tab: FeatureTab) => void
  readonly onNewAttack: () => void
}

export default function FeatureNavigation({ feature, selectedTab, onTabChange, onNewAttack }: FeatureNavigationProps) {
  const styles = useFeatureNavigationStyles()
  const tabs = feature === 'attacks' ? ATTACK_TABS : SCANNER_TABS
  return (
    <header className={styles.root} data-tour="feature-tabs">
      <div className={styles.tabs}>
        <Text as="h1" size={500} weight="semibold">{feature === 'attacks' ? 'Attacks' : 'Scanner'}</Text>
        <TabList
          aria-label={feature === 'attacks' ? 'Attack views' : 'Scanner views'}
          selectedValue={selectedTab}
          onTabSelect={(_: SelectTabEvent, data: SelectTabData) => {
            const tab = tabs.find((option) => option.value === data.value)
            if (tab) {
              onTabChange(tab.value)
            }
          }}
        >
          {tabs.map((tab) => <Tab key={tab.value} value={tab.value}>{tab.label}</Tab>)}
        </TabList>
      </div>
      {feature === 'attacks' && (
        <Button className={styles.action} appearance="primary" icon={<AddRegular />} onClick={onNewAttack}>New attack</Button>
      )}
    </header>
  )
}
