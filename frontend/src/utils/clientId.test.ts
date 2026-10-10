import { generateClientId } from './clientId'

describe('generateClientId', () => {
  const cryptoDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'crypto')
  const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/

  afterEach(() => {
    if (cryptoDescriptor) Object.defineProperty(globalThis, 'crypto', cryptoDescriptor)
    else Reflect.deleteProperty(globalThis, 'crypto')
    jest.restoreAllMocks()
  })

  it('uses the native UUID when available', () => {
    const id = '01234567-89ab-4cde-8f01-23456789abcd'
    const randomUUID = jest.fn(() => id)
    Object.defineProperty(globalThis, 'crypto', { configurable: true, value: { randomUUID } })
    expect(generateClientId()).toBe(id)
    expect(randomUUID).toHaveBeenCalledTimes(1)
  })

  it.each([{}, undefined])('uses the fallback with crypto %p', (cryptoValue: object | undefined) => {
    Object.defineProperty(globalThis, 'crypto', { configurable: true, value: cryptoValue })
    const first = generateClientId()
    const second = generateClientId()
    expect(first).toMatch(uuidPattern)
    expect(second).toMatch(uuidPattern)
    expect(first).not.toBe(second)
  })
})
