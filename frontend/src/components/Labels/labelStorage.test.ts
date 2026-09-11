import { readStoredGlobalLabels, persistGlobalLabels } from './labelStorage'

const STORAGE_KEY = 'pyrit.globalLabels'

describe('labelStorage', () => {
  beforeEach(() => {
    window.localStorage.clear()
    jest.restoreAllMocks()
  })

  it('should round-trip the labels it was given', () => {
    persistGlobalLabels({ operator: 'alice', operation: 'op_2026_08_probe' })

    expect(readStoredGlobalLabels()).toEqual({ operator: 'alice', operation: 'op_2026_08_probe' })
  })

  it('should read nothing when nothing has been stored', () => {
    expect(readStoredGlobalLabels()).toEqual({})
  })

  it('should ignore stored values that are not text', () => {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ operation: 'op_good', bad: 12, worse: null, worst: { a: 1 } }),
    )

    expect(readStoredGlobalLabels()).toEqual({ operation: 'op_good' })
  })

  it.each([
    ['unparseable text', 'not json at all'],
    ['a list', '["op_a"]'],
    ['a bare string', '"op_a"'],
    ['null', 'null'],
  ])('should read nothing when storage holds %s', (_label, stored) => {
    window.localStorage.setItem(STORAGE_KEY, stored)

    expect(readStoredGlobalLabels()).toEqual({})
  })

  it('should read nothing when storage cannot be read', () => {
    jest.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied')
    })

    expect(readStoredGlobalLabels()).toEqual({})
  })

  it('should not throw when storage cannot be written', () => {
    jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota exceeded')
    })

    expect(() => persistGlobalLabels({ operation: 'op_a' })).not.toThrow()
  })
})
