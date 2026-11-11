import { act, render, screen } from '@testing-library/react'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter } from 'react-router'

import { useRuntime } from '@/hooks/useRuntime'
import { promptGeneratorsApi } from '@/services/api'
import { ASSERT_DESCRIPTOR, OBJECTIVE_DESCRIPTOR } from '@/test-utils/promptGenerationFixtures'
import type { PromptGeneratorDescriptor } from '@/types'

import DatasetActions from './DatasetActions'

jest.mock('@/services/api', () => ({ promptGeneratorsApi: { list: jest.fn() } }))
jest.mock('@/hooks/useRuntime', () => ({ useRuntime: jest.fn() }))

const ASSERT_GENERATOR: PromptGeneratorDescriptor = { ...ASSERT_DESCRIPTOR, follow_up_scenario: 'foundry.assert_policy' }
const mockList = jest.mocked(promptGeneratorsApi.list)

type ListResult = { items: PromptGeneratorDescriptor[] }

function deferredList(): { resolve: (result: ListResult) => void, reject: (error: Error) => void } {
  const handlers = { resolve: (_: ListResult): void => {}, reject: (_: Error): void => {} }
  mockList.mockImplementationOnce(() => new Promise<ListResult>((resolve, reject) => {
    handlers.resolve = resolve
    handlers.reject = reject
  }))
  return {
    resolve: (result: ListResult) => handlers.resolve(result),
    reject: (error: Error) => handlers.reject(error),
  }
}

function actionsTree(scenarioName: string) {
  return <FluentProvider theme={webLightTheme}><MemoryRouter>
    <DatasetActions scenarioName={scenarioName} />
  </MemoryRouter></FluentProvider>
}

function setGeneration(generation: string, ready = true): void {
  jest.mocked(useRuntime).mockReturnValue({ ready, state: ready ? 'ready' : 'unavailable', generation })
}

function generateLink(): HTMLElement {
  return screen.getByRole('link', { name: 'Generate datasets' })
}

describe('DatasetActions', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    setGeneration('first')
  })

  it('should not guess a destination before the generators load', async () => {
    const list = deferredList()
    render(actionsTree('foundry.assert_policy'))

    expect(screen.getByRole('link', { name: 'Browse datasets' })).toHaveAttribute('href', '/datasets')
    expect(screen.getByRole('button', { name: 'Generate datasets' })).toHaveAttribute('aria-disabled', 'true')
    expect(screen.queryByRole('link', { name: 'Generate datasets' })).not.toBeInTheDocument()

    await act(async () => { list.resolve({ items: [ASSERT_GENERATOR, OBJECTIVE_DESCRIPTOR] }) })
    expect(generateLink()).toHaveAttribute('href', '/datasets/generate/new?generator=assert')
  })

  it('should not reuse the previous runtime generators while they reload', async () => {
    mockList.mockResolvedValueOnce({ items: [ASSERT_GENERATOR] })
    const view = render(actionsTree('foundry.assert_policy'))
    expect(await screen.findByRole('link', { name: 'Generate datasets' }))
      .toHaveAttribute('href', '/datasets/generate/new?generator=assert')

    const list = deferredList()
    setGeneration('second')
    view.rerender(actionsTree('foundry.assert_policy'))

    expect(screen.getByRole('button', { name: 'Generate datasets' })).toHaveAttribute('aria-disabled', 'true')
    await act(async () => { list.resolve({ items: [{ ...ASSERT_GENERATOR, follow_up_scenario: null }] }) })
    expect(generateLink()).toHaveAttribute('href', '/datasets/generate/new')
  })

  it('should hide the previous generator while the runtime is unavailable under the same generation', async () => {
    mockList.mockResolvedValueOnce({ items: [ASSERT_GENERATOR] })
    const view = render(actionsTree('foundry.assert_policy'))
    expect(await screen.findByRole('link', { name: 'Generate datasets' }))
      .toHaveAttribute('href', '/datasets/generate/new?generator=assert')

    setGeneration('first', false)
    view.rerender(actionsTree('foundry.assert_policy'))

    expect(screen.getByRole('button', { name: 'Generate datasets' })).toHaveAttribute('aria-disabled', 'true')
    expect(screen.getByRole('link', { name: 'Browse datasets' })).toHaveAttribute('href', '/datasets')

    const list = deferredList()
    setGeneration('second')
    view.rerender(actionsTree('foundry.assert_policy'))

    expect(screen.getByRole('button', { name: 'Generate datasets' })).toHaveAttribute('aria-disabled', 'true')
    await act(async () => { list.resolve({ items: [OBJECTIVE_DESCRIPTOR] }) })
    expect(generateLink()).toHaveAttribute('href', '/datasets/generate/new')
  })

  it('should open the chooser when the generators cannot be loaded', async () => {
    const list = deferredList()
    render(actionsTree('foundry.assert_policy'))

    await act(async () => { list.reject(new Error('Generators unavailable')) })
    expect(generateLink()).toHaveAttribute('href', '/datasets/generate/new')
  })

  it.each([
    ['no generator runs the scenario', 'foundry.red_team_agent', [ASSERT_GENERATOR]],
    ['several generators run the scenario', 'foundry.assert_policy', [ASSERT_GENERATOR, { ...ASSERT_GENERATOR, name: 'assert_v2' }]],
  ])('should open the chooser when %s', async (_: string, scenarioName: string, items: PromptGeneratorDescriptor[]) => {
    mockList.mockResolvedValueOnce({ items })
    render(actionsTree(scenarioName))

    expect(await screen.findByRole('link', { name: 'Generate datasets' })).toHaveAttribute('href', '/datasets/generate/new')
  })
})
