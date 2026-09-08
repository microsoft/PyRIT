import {
  attackConversationRoutePath,
  attackRoutePath,
  routerPathParamValue,
  scenarioRunAttackRoutePath,
  scenarioRunProvenance,
  scenarioRunRoutePath,
} from './routeParams'

const SCENARIO_RESULT_ID = '123e4567-e89b-12d3-a456-426614174000'

describe('routerPathParamValue', () => {
  it('returns an empty value for a missing route parameter', () => {
    expect(routerPathParamValue(undefined)).toBe('')
  })

  it('restores slashes re-escaped by React Router', () => {
    expect(routerPathParamValue('foundry%2Fred_team_agent')).toBe('foundry/red_team_agent')
  })

  it('preserves literal and malformed percent sequences', () => {
    expect(routerPathParamValue('discount%50')).toBe('discount%50')
    expect(routerPathParamValue('%zz')).toBe('%zz')
  })
})

describe('scenario run provenance routes', () => {
  it('reads one canonical UUID and ignores unrelated query values', () => {
    const params = new URLSearchParams(`tab=messages&scenarioResultId=${SCENARIO_RESULT_ID}`)

    expect(scenarioRunProvenance(params)).toBe(SCENARIO_RESULT_ID)
  })

  it.each([
    '',
    'scenarioResultId=run-1',
    'scenarioResultId=https%3A%2F%2Fevil.example%2Freturn',
    `scenarioResultId=${'a'.repeat(100)}`,
    `scenarioResultId=${SCENARIO_RESULT_ID}&scenarioResultId=${SCENARIO_RESULT_ID}`,
  ])('rejects missing, unsafe, or ambiguous provenance: %s', (query: string) => {
    expect(scenarioRunProvenance(new URLSearchParams(query))).toBeNull()
  })

  it('builds encoded attack and conversation destinations with bounded provenance', () => {
    expect(attackRoutePath('attack/1', SCENARIO_RESULT_ID)).toBe(
      `/attacks/attack%2F1?scenarioResultId=${SCENARIO_RESULT_ID}`,
    )
    expect(attackConversationRoutePath('attack/1', 'conversation/1', SCENARIO_RESULT_ID)).toBe(
      `/attacks/attack%2F1/conversations/conversation%2F1?scenarioResultId=${SCENARIO_RESULT_ID}`,
    )
  })

  it('omits invalid provenance instead of serializing it', () => {
    expect(attackRoutePath('attack-1', 'https://evil.example')).toBe('/attacks/attack-1')
    expect(attackConversationRoutePath('attack-1', 'conversation-1', 'run-1')).toBe(
      '/attacks/attack-1/conversations/conversation-1',
    )
  })

  it('builds an encoded scenario-run route from a trusted persisted ID', () => {
    expect(scenarioRunRoutePath('run/1')).toBe('/scanner-history/run%2F1')
    expect(scenarioRunAttackRoutePath('run/1', 'attack/1')).toBe(
      '/scanner-history/run%2F1/attack%2F1',
    )
  })
})
