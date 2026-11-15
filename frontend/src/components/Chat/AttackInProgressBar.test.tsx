import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'

import AttackInProgressBar from './AttackInProgressBar'

function Destination() {
  const location = useLocation()
  return <h1 data-state={JSON.stringify(location.state)}>Scan</h1>
}

describe('AttackInProgressBar', () => {
  it('returns a metadata-only fresh tab to scan history with fallback navigation state', async () => {
    const user = userEvent.setup()
    render(<FluentProvider theme={webLightTheme}><MemoryRouter initialEntries={['/chat']}>
      <Routes>
        <Route path="/chat" element={<AttackInProgressBar scanPath="/scan" />} />
        <Route path="/scan" element={<Destination />} />
      </Routes>
    </MemoryRouter></FluentProvider>)
    await user.click(screen.getByRole('link', { name: 'Back to scan' }))
    expect(screen.getByRole('heading', { name: 'Scan' })).toHaveAttribute('data-state', '{"fromScenarioHistory":true}')
  })

  it('uses the return callback for ordinary clicks but preserves modified link navigation', async () => {
    const user = userEvent.setup()
    const onReturnToScan = jest.fn()
    render(<FluentProvider theme={webLightTheme}><MemoryRouter>
      <AttackInProgressBar scanPath="/scan" onReturnToScan={onReturnToScan} />
    </MemoryRouter></FluentProvider>)
    const link = screen.getByRole('link', { name: 'Back to scan' })
    await user.click(link)
    expect(onReturnToScan).toHaveBeenCalledTimes(1)
    await user.keyboard('{Control>}')
    await user.click(link)
    await user.keyboard('{/Control}')
    expect(onReturnToScan).toHaveBeenCalledTimes(1)
    expect(link).toHaveAttribute('href', '/scan')
  })
})
