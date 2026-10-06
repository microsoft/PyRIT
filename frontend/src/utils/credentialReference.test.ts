import { environmentVariableError } from './credentialReference'

describe('environmentVariableError', () => {
  it('accepts a blank or valid variable name', () => {
    expect(environmentVariableError('')).toBeNull()
    expect(environmentVariableError('AZURE_SPEECH_KEY')).toBeNull()
    expect(environmentVariableError('_private_2')).toBeNull()
  })

  it('names the rule a variable name breaks', () => {
    expect(environmentVariableError('2FAST')).toBe('Use letters, digits, and underscores, not starting with a digit.')
    expect(environmentVariableError('MY-KEY')).not.toBeNull()
    expect(environmentVariableError('sk-live-value')).not.toBeNull()
  })
})
