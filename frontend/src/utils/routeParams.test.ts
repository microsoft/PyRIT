import { routerPathParamValue } from './routeParams'

describe('routerPathParamValue', () => {
  it('restores slashes re-escaped by React Router', () => {
    expect(routerPathParamValue('foundry%2Fred_team_agent')).toBe('foundry/red_team_agent')
  })

  it('preserves literal and malformed percent sequences', () => {
    expect(routerPathParamValue('discount%50')).toBe('discount%50')
    expect(routerPathParamValue('%zz')).toBe('%zz')
  })
})
