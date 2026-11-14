import { useState } from 'react'
import { createRoot } from 'react-dom/client'
import { Button, FluentProvider, MessageBar, MessageBarActions, MessageBarBody, webLightTheme } from '@fluentui/react-components'
import { DismissRegular } from '@fluentui/react-icons'

import EditorWorkspace from '@/components/EditorWorkspace'
import { useConfigurationStyles } from '@/components/Configuration/Configuration.styles'

function Fixture() {
  const styles = useConfigurationStyles()
  const [selected, setSelected] = useState('first')
  return (
    <FluentProvider theme={webLightTheme} style={{ height: '100vh' }}>
      <div className={styles.root}>
        <div style={{ minHeight: '16rem' }}>Configuration</div>
        <div className={styles.environmentSection}>
          <MessageBar intent="warning">
            <MessageBarBody>Added and registered an initializer, but failed to refresh catalog: Custom initializer catalog is temporarily unavailable.</MessageBarBody>
            <MessageBarActions>
              <Button className={styles.noticeDismiss}>Retry refresh</Button>
              <Button className={styles.noticeDismiss} icon={<DismissRegular />} aria-label="Dismiss notification" />
            </MessageBarActions>
          </MessageBar>
          <EditorWorkspace
            items={[
              { id: 'first', label: 'first', secondaryText: '~/.pyrit/initializers/first.py' },
              { id: 'second', label: 'second', secondaryText: '~/.pyrit/initializers/second.py' },
            ]}
            selectedId={selected}
            navigationLabel="Custom initializer files"
            emptyMessage="No initializers"
            description="Python initializers loaded when PyRIT starts."
            actions={<Button>Add initializer</Button>}
            onSelect={setSelected}
          >
            <div style={{ minHeight: '24rem' }}>{selected} source</div>
          </EditorWorkspace>
        </div>
      </div>
    </FluentProvider>
  )
}

const root = document.getElementById('root')
if (!root) throw new Error('Missing fixture root')
createRoot(root).render(<Fixture />)
