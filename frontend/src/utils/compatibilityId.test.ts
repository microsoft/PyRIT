import { isCompatibilityId } from './compatibilityId'

const bundledId = `1.2.0.dev0+g${'a'.repeat(40)}`

describe('compatibility identity', () => {
  it.each([
    undefined, null, 42, '', '1.2.0', `1.2.0+g${'a'.repeat(7)}`,
    `1.2.0+g${'A'.repeat(40)}`, `1.2.0+g${'a'.repeat(41)}`,
    `1.2.0+g${'a'.repeat(40)}-dirty`, ` ${bundledId}`, `${bundledId}\n`,
    `1+g${'a'.repeat(40)}`, `1.2.0+local+g${'a'.repeat(40)}`, `1.2.0${'a'.repeat(220)}+g${'a'.repeat(40)}`,
    `1.2.0junk+g${'a'.repeat(40)}`, `1.2.0.dev+g${'a'.repeat(40)}`, `1.2.0RC1+g${'a'.repeat(40)}`,
    `${bundledId}\r`, `${bundledId}\r\n`,
  ])('rejects malformed identity %p', (identity: unknown) => {
    expect(isCompatibilityId(identity)).toBe(false)
  })

  it('accepts version plus full lowercase commit', () => {
    expect(isCompatibilityId(bundledId)).toBe(true)
  })

  it.each(['1.2.0', '1.2.0a1', '1.2.0b2', '1.2.0rc3', '1.2.0.post1', '1.2.0rc1.post2.dev3'])(
    'accepts normalized version %s', (version: string) => {
      expect(isCompatibilityId(`${version}+g${'a'.repeat(40)}`)).toBe(true)
    },
  )

  it('enforces the 256-character limit even for well-formed identities', () => {
    const identity = `${'1'.repeat(210)}.2.0+g${'a'.repeat(40)}`
    expect(isCompatibilityId(identity)).toBe(true)
    expect(isCompatibilityId(`1${identity}`)).toBe(false)
  })
})
