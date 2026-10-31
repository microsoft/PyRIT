import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import { makeTarget } from '@/test-utils/targetFixtures'
import type {
  PresetIssue,
  ScenarioPresetResponse,
  ScenarioSuiteEntryStatus,
  ScenarioSuiteResponse,
  TargetInstance,
} from '@/types'

import ScannerSuiteSection from './ScannerSuiteSection'

interface LaunchSuiteDialogStubProps {
  suite: ScenarioSuiteResponse
  targets: TargetInstance[]
  defaultObjectiveTarget: TargetInstance | null
  defaultAdversarialTarget: TargetInstance | null
  labels: Record<string, string>
  operationId: string | null
  defaultsReady: boolean
  onDismiss: () => void
  onSuiteChanged: () => void
}

jest.mock('@/components/ScenarioSuites/LaunchSuiteDialog', () => ({
  __esModule: true,
  default: ({
    suite,
    targets,
    defaultObjectiveTarget,
    defaultAdversarialTarget,
    labels,
    operationId,
    defaultsReady,
    onDismiss,
    onSuiteChanged,
  }: LaunchSuiteDialogStubProps) => (
    <div
      role="dialog"
      aria-label={`Launch ${suite.suite.name}`}
      data-targets={targets.map((target) => target.target_registry_name).join(',')}
      data-objective-target={defaultObjectiveTarget?.target_registry_name ?? 'none'}
      data-adversarial-target={defaultAdversarialTarget?.target_registry_name ?? 'none'}
      data-labels={JSON.stringify(labels)}
      data-operation-id={operationId ?? 'none'}
      data-defaults-ready={String(defaultsReady)}
    >
      <button type="button" onClick={onDismiss}>Dismiss launch</button>
      <button type="button" onClick={onSuiteChanged}>Report suite change</button>
    </div>
  ),
}))

type ScannerSuiteSectionProps = React.ComponentProps<typeof ScannerSuiteSection>

interface SuiteOptions {
  description?: string | null
  entryIssues?: PresetIssue[][]
}

const OBJECTIVE_TARGET = makeTarget({ target_registry_name: 'objective-target' })
const ADVERSARIAL_TARGET = makeTarget({ target_registry_name: 'adversarial-target' })

function TestWrapper({ children }: { children: React.ReactNode }) {
  return <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
}

function makePreset(name: string, scenarioName: string): ScenarioPresetResponse {
  return {
    preset: { name, scenario_name: scenarioName },
    origin: 'stored',
    version: 'v1',
    issues: [],
  }
}

function makeSuite(name: string, presetNames: string[], options: SuiteOptions = {}): ScenarioSuiteResponse {
  const entries = presetNames.map((presetName, index) => ({
    entry_id: `${name}-entry-${index + 1}`,
    preset_name: presetName,
  }))
  return {
    suite: { name, schema_version: 1, description: options.description ?? null, entries },
    origin: 'stored',
    version: 'v1',
    entry_statuses: entries.map((entry, index): ScenarioSuiteEntryStatus => ({
      entry_id: entry.entry_id,
      preset_name: entry.preset_name,
      origin: 'stored',
      preset_version: 'v1',
      issues: options.entryIssues?.[index] ?? [],
    })),
  }
}

const PRESETS: ReadonlyMap<string, ScenarioPresetResponse> = new Map([
  ['quick-jailbreak', makePreset('quick-jailbreak', 'airt.jailbreak')],
  ['hidden-encoding', makePreset('hidden-encoding', 'garak.encoding')],
])

describe('ScannerSuiteSection', () => {
  const defaultProps: ScannerSuiteSectionProps = {
    suites: [makeSuite('nightly', ['quick-jailbreak', 'hidden-encoding'])],
    presets: PRESETS,
    targets: [OBJECTIVE_TARGET, ADVERSARIAL_TARGET],
    defaultObjectiveTarget: OBJECTIVE_TARGET,
    defaultAdversarialTarget: ADVERSARIAL_TARGET,
    labels: { operator: 'red-team' },
    operationId: 'operation-1',
    defaultsReady: true,
    onSuiteChanged: jest.fn(),
  }

  function renderSection(props: Partial<ScannerSuiteSectionProps> = {}): void {
    render(
      <TestWrapper>
        <ScannerSuiteSection {...defaultProps} {...props} />
      </TestWrapper>,
    )
  }

  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('renders the suites in a labelled table in the order given', () => {
    renderSection({
      suites: [
        makeSuite('nightly', ['quick-jailbreak', 'hidden-encoding'], { description: 'Runs every night.' }),
        makeSuite('smoke', ['quick-jailbreak']),
      ],
    })

    const heading = screen.getByRole('heading', { level: 2, name: 'Scenario suites' })
    expect(screen.getByTestId('scanner-suites')).toHaveAttribute('aria-labelledby', heading.id)
    const table = screen.getByRole('table', { name: 'Scenario suites' })
    expect(within(table).getAllByRole('columnheader').map((header) => header.textContent)).toEqual([
      'Suite / purpose',
      'Scenarios',
      'Actions',
    ])
    expect(
      within(table).getAllByRole('row').slice(1).map((row) => row.getAttribute('data-testid')),
    ).toEqual(['suite-card-nightly', 'suite-card-smoke'])

    const nightly = screen.getByTestId('suite-card-nightly')
    expect(within(nightly).getByText('nightly')).toBeInTheDocument()
    expect(within(nightly).getByText('Runs every night.')).toBeInTheDocument()
    expect(within(nightly).getByText('2 scenarios')).toBeInTheDocument()
    expect(within(nightly).queryByText('Needs attention')).not.toBeInTheDocument()
    expect(screen.queryByTestId('suite-issues-nightly')).not.toBeInTheDocument()

    const smoke = screen.getByTestId('suite-card-smoke')
    expect(within(smoke).getByText('smoke')).toBeInTheDocument()
    expect(within(smoke).getByText('1 scenario')).toBeInTheDocument()
    expect(within(smoke).queryByText('Runs every night.')).not.toBeInTheDocument()
  })

  it('reveals the presets a suite runs and the scenario behind each known preset', async () => {
    const user = userEvent.setup()
    renderSection({ suites: [makeSuite('nightly', ['quick-jailbreak', 'deleted-preset', 'hidden-encoding'])] })

    const showButton = screen.getByRole('button', { name: 'Show scenarios in nightly' })
    const members = screen.getByRole('list', { hidden: true })
    expect(members).toHaveAttribute('aria-label', 'Scenarios in nightly')
    expect(showButton).toHaveAttribute('aria-expanded', 'false')
    expect(showButton).toHaveAttribute('aria-controls', members.id)
    expect(members).toHaveAttribute('hidden')
    expect(members).not.toBeVisible()
    expect(screen.queryByRole('list', { name: 'Scenarios in nightly' })).not.toBeInTheDocument()

    await user.click(showButton)

    const hideButton = screen.getByRole('button', { name: 'Hide scenarios in nightly' })
    expect(hideButton).toHaveAttribute('aria-expanded', 'true')
    const visibleMembers = screen.getByRole('list', { name: 'Scenarios in nightly' })
    expect(visibleMembers).toBeVisible()
    expect(within(visibleMembers).getAllByRole('listitem').map((item) => item.textContent)).toEqual([
      'quick-jailbreak · Runs airt.jailbreak',
      'deleted-preset',
      'hidden-encoding · Runs garak.encoding',
    ])

    await user.click(hideButton)

    expect(screen.getByRole('button', { name: 'Show scenarios in nightly' })).toHaveAttribute(
      'aria-expanded',
      'false',
    )
    expect(members).toHaveAttribute('hidden')
    expect(screen.queryByRole('list', { name: 'Scenarios in nightly' })).not.toBeInTheDocument()
  })

  it('lists entry issues and blocks launch for a suite that needs attention', () => {
    renderSection({
      suites: [
        makeSuite('broken', ['quick-jailbreak', 'deleted-preset'], {
          entryIssues: [
            [{ field: 'techniques', message: 'Technique flip is not available.' }],
            [{ field: 'preset_name', message: 'Preset deleted-preset does not exist.' }],
          ],
        }),
      ],
    })

    const row = screen.getByTestId('suite-card-broken')
    expect(within(row).getByText('Needs attention')).toBeInTheDocument()
    expect(
      within(screen.getByTestId('suite-issues-broken')).getAllByRole('listitem').map((item) => item.textContent),
    ).toEqual([
      'Entry 1 (quick-jailbreak): Technique flip is not available.',
      'Entry 2 (deleted-preset): Preset deleted-preset does not exist.',
    ])
    expect(within(row).getByRole('button', { name: 'Launch' })).toBeDisabled()
  })

  it('omits the scenario list and blocks launch for a suite without entries', () => {
    renderSection({ suites: [makeSuite('empty', [])] })

    const row = screen.getByTestId('suite-card-empty')
    expect(within(row).getByText('0 scenarios')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /scenarios in empty/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('list', { hidden: true })).not.toBeInTheDocument()
    expect(screen.getByTestId('launch-suite-empty')).toBeDisabled()
  })

  it('blocks launch until the launch defaults are ready', () => {
    renderSection({ defaultsReady: false })

    expect(screen.getByTestId('launch-suite-nightly')).toBeDisabled()
  })

  it('opens the launch dialog for the chosen suite with the launch defaults', async () => {
    const user = userEvent.setup()
    renderSection({
      suites: [makeSuite('nightly', ['quick-jailbreak']), makeSuite('smoke', ['hidden-encoding'])],
    })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

    const launchButton = within(screen.getByTestId('suite-card-smoke')).getByRole('button', { name: 'Launch' })
    expect(launchButton).toBeEnabled()
    await user.click(launchButton)

    const dialog = screen.getByRole('dialog', { name: 'Launch smoke' })
    expect(dialog).toHaveAttribute('data-targets', 'objective-target,adversarial-target')
    expect(dialog).toHaveAttribute('data-objective-target', 'objective-target')
    expect(dialog).toHaveAttribute('data-adversarial-target', 'adversarial-target')
    expect(dialog).toHaveAttribute('data-labels', JSON.stringify({ operator: 'red-team' }))
    expect(dialog).toHaveAttribute('data-operation-id', 'operation-1')
    expect(dialog).toHaveAttribute('data-defaults-ready', 'true')
  })

  it('closes the launch dialog without reporting a change when dismissed', async () => {
    const user = userEvent.setup()
    const onSuiteChanged = jest.fn()
    renderSection({ onSuiteChanged })

    await user.click(screen.getByTestId('launch-suite-nightly'))
    await user.click(screen.getByRole('button', { name: 'Dismiss launch' }))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(onSuiteChanged).not.toHaveBeenCalled()
  })

  it('closes the launch dialog and reports the suite that changed during launch', async () => {
    const user = userEvent.setup()
    const onSuiteChanged = jest.fn()
    renderSection({ onSuiteChanged })

    await user.click(screen.getByTestId('launch-suite-nightly'))
    await user.click(screen.getByRole('button', { name: 'Report suite change' }))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(onSuiteChanged).toHaveBeenCalledTimes(1)
    expect(onSuiteChanged).toHaveBeenCalledWith('nightly')
  })
})
