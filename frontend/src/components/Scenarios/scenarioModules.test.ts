import { makeScenario } from '@/test-utils/scenarioFixtures'
import type { RegisteredScenario, ScenarioModule } from '@/types'

import {
  filterModuleGroups,
  groupScenariosByModule,
  OTHER_SCENARIOS_KEY,
  shortScenarioName,
} from './scenarioModules'

const MODULES: ScenarioModule[] = [
  { name: 'airt', display_name: 'AI Red Team', description: 'Harm-area scenarios.' },
  { name: 'empty', display_name: 'Empty', description: 'No registered scenarios.' },
  { name: 'garak', display_name: 'Garak', description: 'Ports of garak probes: prompt injection.' },
]

function scenario(name: string, module: string | null, description = 'Probes a target.'): RegisteredScenario {
  return makeScenario({ scenario_name: name, module, description })
}

const SCENARIOS = [
  scenario('garak.web_injection', 'garak', 'Injects instructions through web content.'),
  scenario('my_custom_probe', null),
  scenario('airt.jailbreak', 'airt'),
  scenario('garak.encoding', 'garak', 'Hides objectives in encodings.'),
]

describe('groupScenariosByModule', () => {
  it('keeps the API module order, sorts scenarios by name, and puts other scenarios last', () => {
    const groups = groupScenariosByModule(SCENARIOS, MODULES)

    expect(groups.map((group) => [group.key, group.title])).toEqual([
      ['airt', 'AI Red Team'],
      ['garak', 'Garak'],
      [OTHER_SCENARIOS_KEY, 'Other scenarios'],
    ])
    expect(groups[1].description).toBe('Ports of garak probes: prompt injection.')
    expect(groups[1].scenarios.map((item) => item.scenario_name)).toEqual(['garak.encoding', 'garak.web_injection'])
    expect(groups[2].scenarios.map((item) => item.scenario_name)).toEqual(['my_custom_probe'])
  })

  it('omits the other card when every scenario has a module', () => {
    const groups = groupScenariosByModule([scenario('airt.jailbreak', 'airt')], MODULES)
    expect(groups.map((group) => group.key)).toEqual(['airt'])
  })

  it('still shows a module the module list does not describe, named by its package', () => {
    const groups = groupScenariosByModule([scenario('newer.probe', 'newer')], MODULES)
    expect(groups).toEqual([
      { key: 'newer', title: 'newer', description: '', scenarios: [expect.objectContaining({ module: 'newer' })] },
    ])
  })
})

describe('shortScenarioName', () => {
  it.each([
    ['garak.encoding', 'garak', 'encoding'],
    ['airt.nested.leakage', 'airt', 'nested.leakage'],
    ['my_custom_probe', null, 'my_custom_probe'],
    ['renamed.probe', 'garak', 'renamed.probe'],
  ])('shows %s in module %s as %s', (name, module, expected) => {
    expect(shortScenarioName(scenario(name, module))).toBe(expected)
  })
})

describe('filterModuleGroups', () => {
  const groups = groupScenariosByModule(SCENARIOS, MODULES)

  function matchNames(query: string): Array<[string, string[]]> {
    return filterModuleGroups(groups, query)
      .map(({ group, matches }) => [group.key, matches.map((item) => item.scenario_name)])
  }

  it('lists every scenario without a query', () => {
    expect(matchNames('  ')).toEqual([
      ['airt', ['airt.jailbreak']],
      ['garak', ['garak.encoding', 'garak.web_injection']],
      [OTHER_SCENARIOS_KEY, ['my_custom_probe']],
    ])
  })

  it('keeps only modules with matching scenarios', () => {
    expect(matchNames('Encodings')).toEqual([['garak', ['garak.encoding']]])
  })

  it.each(['garak', 'GARAK', 'red team'])('lists the whole module when %p names it', (query) => {
    const [first] = filterModuleGroups(groups, query)
    expect(first.matches).toEqual(first.group.scenarios)
  })

  it('never matches a module description', () => {
    expect(matchNames('prompt injection')).toEqual([])
  })

  it('does not treat the other card title as a module name', () => {
    expect(matchNames('other')).toEqual([])
  })
})
