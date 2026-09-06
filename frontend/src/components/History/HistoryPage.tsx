import type { ReactNode } from 'react'

import { Tab, TabList, Text } from '@fluentui/react-components'
import type { SelectTabData, SelectTabEvent } from '@fluentui/react-components'

import { useHistoryPageStyles } from './HistoryPage.styles'

export type HistoryTab = 'attacks' | 'scanner'

interface HistoryPageProps {
  readonly selectedTab: HistoryTab
  readonly onTabChange: (tab: HistoryTab) => void
  readonly children: ReactNode
}

export default function HistoryPage({ selectedTab, onTabChange, children }: HistoryPageProps) {
  const styles = useHistoryPageStyles()

  const handleTabSelect = (_: SelectTabEvent, data: SelectTabData): void => {
    if (data.value === 'attacks' || data.value === 'scanner') {
      onTabChange(data.value)
    }
  }

  return (
    <main className={styles.root}>
      <header className={styles.header}>
        <Text as="h1" size={600} weight="semibold">History</Text>
        <TabList selectedValue={selectedTab} onTabSelect={handleTabSelect}>
          <Tab value="attacks">Attacks</Tab>
          <Tab value="scanner">Scanner</Tab>
        </TabList>
      </header>
      <div className={styles.content}>{children}</div>
    </main>
  )
}
