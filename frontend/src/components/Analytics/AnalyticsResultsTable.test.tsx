import type { ComponentProps, ReactNode } from 'react'

import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { makeAnalyticsResults, makeAnalyticsRow } from '@/test-utils/analyticsFixtures'

import AnalyticsResultsTable from './AnalyticsResultsTable'

function TestWrapper({ children }: { readonly children: ReactNode }) {
  return <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
}

function renderTable(overrides: Partial<ComponentProps<typeof AnalyticsResultsTable>> = {}) {
  const props: ComponentProps<typeof AnalyticsResultsTable> = {
    results: makeAnalyticsResults(),
    totalResults: 10,
    page: 0,
    loading: false,
    disabled: false,
    error: null,
    onFirst: jest.fn(),
    onNext: jest.fn(),
    onRetry: jest.fn(),
    onOpenAttack: jest.fn(),
    ...overrides,
  }
  render(<TestWrapper><AnalyticsResultsTable {...props} /></TestWrapper>)
  return props
}

describe('AnalyticsResultsTable', () => {
  beforeEach(() => { jest.clearAllMocks() })

  it('opens the correct result when any cell in its row is clicked', async () => {
    const user = userEvent.setup()
    const { onOpenAttack } = renderTable({
      results: makeAnalyticsResults({
        items: [makeAnalyticsRow(), makeAnalyticsRow({ attack_result_id: 'result-2', operator: 'Bob' })],
      }),
    })
    const row = screen.getByRole('row', { name: 'Open result result-2' })
    await user.click(within(row).getByRole('cell', { name: 'Bob' }))
    expect(onOpenAttack).toHaveBeenCalledTimes(1)
    expect(onOpenAttack).toHaveBeenCalledWith('result-2')
    expect(screen.queryByRole('button', { name: /^Open result / })).not.toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: 'Open' })).not.toBeInTheDocument()
  })

  it.each(['{Enter}', ' '])('opens a focused row using %s', async (key: string) => {
    const user = userEvent.setup()
    const { onOpenAttack } = renderTable()
    const row = screen.getByRole('row', { name: 'Open result result-1' })
    row.focus()
    expect(row).toHaveFocus()
    expect(row).toHaveAccessibleDescription('Inspect a saved response')
    await user.keyboard(key)
    expect(onOpenAttack).toHaveBeenCalledTimes(1)
    expect(onOpenAttack).toHaveBeenCalledWith('result-1')
  })

  it('does not activate a row for navigation keys', async () => {
    const user = userEvent.setup()
    const { onOpenAttack } = renderTable()
    screen.getByRole('row', { name: 'Open result result-1' }).focus()
    await user.keyboard('{ArrowDown}')
    expect(onOpenAttack).not.toHaveBeenCalled()
  })

  it('keeps the retry control separate from row navigation', async () => {
    const user = userEvent.setup()
    const { onRetry, onOpenAttack } = renderTable({ error: 'Retry this request.' })
    await user.click(screen.getByRole('button', { name: 'Retry results' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
    expect(onOpenAttack).not.toHaveBeenCalled()
  })
})
