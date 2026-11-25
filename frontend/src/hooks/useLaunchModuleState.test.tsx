import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation, useNavigationType } from 'react-router'

import { useLaunchModuleState } from './useLaunchModuleState'

interface ModuleControlsProps {
  next: string[]
}

function ModuleControls({ next }: ModuleControlsProps) {
  const { openModules, setOpenModules } = useLaunchModuleState()
  const location = useLocation()
  const navigationType = useNavigationType()
  return (
    <>
      <output aria-label="Open modules">{openModules.join(',')}</output>
      <output aria-label="Search">{location.search}</output>
      <output aria-label="Navigation">{navigationType}</output>
      <button type="button" onClick={() => setOpenModules(next)}>Set</button>
    </>
  )
}

function renderAt(entry: string, next: string[]) {
  render(
    <MemoryRouter initialEntries={[entry]}>
      <ModuleControls next={next} />
    </MemoryRouter>,
  )
}

describe('useLaunchModuleState', () => {
  it('reads the open modules from the URL', () => {
    renderAt('/scanner/launch?open=airt&open=garak', [])
    expect(screen.getByRole('status', { name: 'Open modules' })).toHaveTextContent('airt,garak')
  })

  it('writes the open modules to the URL in place, keeping other parameters', async () => {
    const user = userEvent.setup()
    renderAt('/scanner/launch?open=airt&tour=1', ['garak', 'foundry', 'garak'])

    await user.click(screen.getByRole('button', { name: 'Set' }))

    expect(screen.getByRole('status', { name: 'Open modules' })).toHaveTextContent('garak,foundry')
    expect(screen.getByRole('status', { name: 'Search' })).toHaveTextContent('?tour=1&open=garak&open=foundry')
    expect(screen.getByRole('status', { name: 'Navigation' })).toHaveTextContent('REPLACE')
  })

  it('removes the parameter when every module is closed', async () => {
    const user = userEvent.setup()
    renderAt('/scanner/launch?open=airt', [])

    await user.click(screen.getByRole('button', { name: 'Set' }))

    expect(screen.getByRole('status', { name: 'Search' })).toBeEmptyDOMElement()
  })
})
