import type { RegisteredScenario } from '../types'

/**
 * Builds a `RegisteredScenario` for tests. Technique fields are derived from
 * one another so a test can override just `all_techniques` (or just
 * `default_techniques`) and still get a self-consistent scenario.
 */
export function makeScenario(overrides: Partial<RegisteredScenario> = {}): RegisteredScenario {
  const description = overrides.description ?? 'Red teams a target.'
  const defaultTechnique = overrides.default_technique ?? 'default'
  const aggregateTechniques = overrides.aggregate_techniques ?? ['all', 'default']
  const defaultTechniques = overrides.default_techniques
    ?? (aggregateTechniques.includes(defaultTechnique) ? ['default_technique'] : [defaultTechnique])
  const allTechniques = overrides.all_techniques ?? ['default_technique', 'crescendo']
  const techniqueSummaries = overrides.technique_summaries ?? allTechniques.map((name) => ({
    name,
    description: `${name} description.`,
    tags: name === 'default_technique' ? ['default', 'single_turn'] : ['multi_turn'],
  }))
  return {
    scenario_name: 'foundry.red_team_agent',
    scenario_type: 'RedTeamAgentScenario',
    scenario_version: 1,
    aggregate_technique_expansions: overrides.aggregate_technique_expansions
      ?? Object.fromEntries(
        aggregateTechniques.map((name) => [name, name === defaultTechnique ? defaultTechniques : []]),
      ),
    all_techniques: allTechniques,
    technique_summaries: techniqueSummaries,
    default_datasets: ['harmbench'],
    baseline_policy: 'enabled',
    include_baseline_by_default: true,
    uses_default_adversarial_target: true,
    supported_parameters: [],
    default_run_size: {
      dataset_size: { kind: 'indeterminate', detail: 'Default sizing is unavailable.' },
      dataset_limit: { state: 'scenario_default' },
      estimated_attack_count: null,
      components: [],
      datasets: [],
      note: 'Default sizing is unavailable.',
    },
    ...overrides,
    description,
    description_markdown: overrides.description_markdown ?? description,
    default_technique: defaultTechnique,
    default_techniques: defaultTechniques,
    aggregate_techniques: aggregateTechniques,
  }
}
