import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import HistoryPage, { type HistoryTab } from './HistoryPage'

function renderPage(onTabChange: (tab: HistoryTab) => void): void {
  render(
    <FluentProvider theme={webLightTheme}>
      <main>
        <HistoryPage selectedTab="attacks" onTabChange={onTabChange}>
          <div>Attack history</div>
        </HistoryPage>
      </main>
    </FluentProvider>,
  )
}

describe('HistoryPage', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('should keep the application main landmark unnested', async () => {
    const user = userEvent.setup()
    const onTabChange = jest.fn()
    renderPage(onTabChange)

    expect(screen.getAllByRole('main')).toHaveLength(1)
    expect(screen.getByRole('heading', { level: 1, name: 'History' })).toBeInTheDocument()
    expect(screen.getByText('Attack history')).toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: 'Scanner' }))
    expect(onTabChange).toHaveBeenCalledWith('scanner')
  })
})
