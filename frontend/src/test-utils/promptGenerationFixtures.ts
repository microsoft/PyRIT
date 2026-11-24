import type {
  AssertBehaviorPreset,
  AssertSuitePreset,
  PromptGenerationDetail,
  PromptGenerationEstimate,
  PromptGenerationPreflightResponse,
  PromptGenerationReview,
  PromptGenerationSummary,
  PromptGeneratorDescriptor,
  TargetCapabilities,
} from '../types'

export const ASSERT_DESCRIPTOR: PromptGeneratorDescriptor = {
  name: 'assert',
  display_name: 'ASSERT policy dataset',
  description: 'Turn a written policy into a taxonomy and seed groups.',
  stages: [
    { name: 'systematization', label: 'Research the behavior', reviewable: false },
    { name: 'taxonomy', label: 'Build the taxonomy', reviewable: false },
    { name: 'dimensions', label: 'Resolve coverage dimensions', reviewable: true },
    { name: 'seed_groups', label: 'Generate seed groups', reviewable: false },
  ],
  target_roles: [
    { role: 'generation', label: 'Generation target', description: 'Writes seed groups.', required: true },
    { role: 'research', label: 'Research target', description: 'Researches the behavior.', required: false },
  ],
  follow_up_scenario: 'foundry.assert_policy',
}

export const OBJECTIVE_DESCRIPTOR: PromptGeneratorDescriptor = {
  name: 'target_objective',
  display_name: 'Objective dataset',
  description: 'Generate a list of distinct attack objectives.',
  stages: [{ name: 'objectives', label: 'Generate objectives', reviewable: false }],
  target_roles: [
    { role: 'generation', label: 'Generation target', description: 'Writes the objectives.', required: true },
  ],
  follow_up_scenario: null,
}

export function makeGenerationSummary(overrides: Partial<PromptGenerationSummary> = {}): PromptGenerationSummary {
  return {
    id: 'gen-1',
    generator: 'assert',
    dataset_name: 'returns_policy',
    status: 'running',
    pause_for_review: false,
    group_id: null,
    group_label: null,
    progress: { completed_stages: ['systematization'], current_stage: 'taxonomy', items_completed: 0, items_total: 25 },
    error: null,
    created_by: 'alice',
    created_at: '2026-11-09T10:00:00Z',
    updated_at: '2026-11-09T10:05:00Z',
    completed_at: null,
    ...overrides,
  }
}

export function makeGenerationDetail(overrides: Partial<PromptGenerationDetail> = {}): PromptGenerationDetail {
  return {
    ...makeGenerationSummary(),
    request: { behavior_name: 'returns', behavior_description: 'Explain return policies.', group_count: 25 },
    targets: { generation: 'gpt' },
    review_completed: false,
    summary: null,
    started_at: '2026-11-09T10:01:00Z',
    ...overrides,
  }
}

export function makeGenerationEstimate(overrides: Partial<PromptGenerationEstimate> = {}): PromptGenerationEstimate {
  return {
    stages: [
      { stage: 'taxonomy', target_role: 'generation', min_calls: 1, max_calls: 1, min_tokens: 1000, max_tokens: 1000 },
      { stage: 'seed_groups', target_role: 'generation', min_calls: 25, max_calls: 30, min_tokens: 9000, max_tokens: 12000 },
    ],
    exact_calls: false,
    notes: ['Web search is not included.'],
    min_calls: 26,
    max_calls: 31,
    low_tokens: 7000,
    high_tokens: 19500,
    ...overrides,
  }
}

export function makePreflight(overrides: Partial<PromptGenerationPreflightResponse> = {}): PromptGenerationPreflightResponse {
  return {
    valid: true,
    errors: [],
    dataset_name_available: true,
    estimate: makeGenerationEstimate(),
    ...overrides,
  }
}

export function makeBehaviorPreset(overrides: Partial<AssertBehaviorPreset> = {}): AssertBehaviorPreset {
  return {
    name: 'sycophancy',
    version: '1.0',
    tags: ['safety'],
    applicable_to: ['assistant'],
    summary: 'Agreeing with users against the evidence.',
    description: '# Sycophancy\n\nThe assistant should not agree with false claims.',
    suggested_judge_presets: [],
    ...overrides,
  }
}

export function makeSuitePreset(overrides: Partial<AssertSuitePreset> = {}): AssertSuitePreset {
  return {
    name: 'travel_planner',
    version: '1.0',
    tags: ['quality'],
    applicable_to: ['travel'],
    summary: 'A travel planner.',
    context: 'The target is a travel planner.',
    behaviors: ['sycophancy', 'prompt_injection'],
    suggested_judge_presets: [],
    ...overrides,
  }
}

export function makeAssertReview(overrides: Partial<PromptGenerationReview> = {}): PromptGenerationReview {
  return {
    generation_id: 'gen-1',
    stage: 'dimensions',
    payload: {
      systematization: { scope: 'Return policy questions.', reasoning_summary: 'Customers ask about returns.' },
      taxonomy: {
        behavior: { name: 'returns', definition: 'Explaining return policies.' },
        definition_of_terms: [{ term: 'order', definition: 'A purchase.', examples: ['order 123'] }],
        behavior_categories: [
          { name: 'policy_question', definition: 'Asks about policy.', examples: ['What is the window?'], permissible: true },
          { name: 'other_customer', definition: 'Asks about another order.', examples: ['Where is Bob’s order?'], permissible: false },
        ],
      },
      dimensions: [{
        name: 'channel',
        description: 'Where the user writes from.',
        levels: [{ name: 'chat', definition: 'Web chat.' }, { name: 'email', definition: 'Email.' }],
      }],
    },
    ...overrides,
  }
}

export function capabilities(overrides: Partial<TargetCapabilities> = {}): TargetCapabilities {
  return {
    supports_multi_turn: true,
    supports_json_schema: true,
    supports_json_output: true,
    supports_system_prompt: true,
    supported_input_modalities: ['text'],
    supported_output_modalities: ['text'],
    ...overrides,
  }
}
