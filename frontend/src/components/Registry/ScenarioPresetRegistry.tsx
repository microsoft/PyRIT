import { useCallback, useState } from 'react'

import { Text } from '@fluentui/react-components'
import { Link } from 'react-router'

import ScenarioPresetLibrary from '@/components/ScenarioPresets/ScenarioPresetLibrary'
import ScenarioSuiteLibrary from '@/components/ScenarioSuites/ScenarioSuiteLibrary'

import { useScenarioPresetRegistryStyles } from './ScenarioPresetRegistry.styles'

/**
 * Defines reusable scenario configurations: suites first, then the individual
 * presets they are built from. Both launch from the Scanner.
 */
export default function ScenarioPresetRegistry() {
  const styles = useScenarioPresetRegistryStyles()
  const [presetsRevision, setPresetsRevision] = useState(0)

  const handlePresetDeleted = useCallback((): void => {
    setPresetsRevision((current) => current + 1)
  }, [])

  return (
    <div className={styles.root} data-testid="scenario-preset-registry">
      <header className={styles.header}>
        <Text as="h1" size={600} weight="semibold">
          Scenario presets
        </Text>
        <Text size={300} className={styles.subtitle}>
          Define reusable scenario configurations here, then launch them from
          the <Link to="/scanner">Scanner</Link>.
        </Text>
      </header>
      <ScenarioSuiteLibrary presetsRevision={presetsRevision} />
      <ScenarioPresetLibrary onPresetDeleted={handlePresetDeleted} />
    </div>
  )
}
