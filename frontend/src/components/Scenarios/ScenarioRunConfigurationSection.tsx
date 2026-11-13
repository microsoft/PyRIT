import { useState } from 'react'

import { Table, TableBody, Text } from '@fluentui/react-components'

import RegistryIdentityRow from '@/components/Registry/RegistryIdentityRow'
import type { ScenarioProgressHeader } from '@/types'

import { useScenarioRunPageStyles } from './ScenarioRunPage.styles'

interface ScenarioRunConfigurationSectionProps {
  readonly run: ScenarioProgressHeader
}

/** The saved scenario identity the scan ran with. */
export default function ScenarioRunConfigurationSection({ run }: ScenarioRunConfigurationSectionProps) {
  const styles = useScenarioRunPageStyles()
  const [identityExpanded, setIdentityExpanded] = useState(false)
  return (
    <section className={styles.section} aria-labelledby="configuration-heading">
      <Text as="h2" id="configuration-heading" size={500} weight="semibold">Scenario configuration</Text>
      {run.scenario_identity ? <Table size="small" aria-label="Scenario configuration"><TableBody>
        <RegistryIdentityRow name={run.scenario_registry_name ?? run.scenario_name} identity={run.scenario_identity}
          columns={1} expanded={identityExpanded} onToggle={() => { setIdentityExpanded((value: boolean) => !value) }} />
      </TableBody></Table> : <Text className={styles.sectionHint}>No saved scenario identity.</Text>}
    </section>
  )
}
