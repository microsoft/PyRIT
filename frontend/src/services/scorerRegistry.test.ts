import { scorersApi } from '@/services/api'
import { listRegisteredScorers } from './scorerRegistry'

jest.mock('@/services/api', () => ({ scorersApi: { listScorers: jest.fn() } }))
const list = jest.mocked(scorersApi.listScorers)

describe('listRegisteredScorers', () => {
  beforeEach(() => { list.mockReset() })
  it('loads all pages before resolving scorer identities', async () => {
    list.mockResolvedValueOnce({ items: [{ scorer_registry_name: 'first', identifier: { class_name: 'Judge', hash: 'a' } }],
      pagination: { has_more: true, next_cursor: 'first' } })
      .mockResolvedValueOnce({ items: [{ scorer_registry_name: 'last', identifier: { class_name: 'Judge', hash: 'b' } }],
        pagination: { has_more: false, next_cursor: null } })
    expect((await listRegisteredScorers()).map((scorer) => scorer.scorer_registry_name)).toEqual(['first', 'last'])
    expect(list).toHaveBeenLastCalledWith(200, 'first')
  })
  it('reports a repeated cursor instead of hanging or accepting a partial catalog', async () => {
    list.mockResolvedValue({ items: [], pagination: { has_more: true, next_cursor: 'same' } })
    await expect(listRegisteredScorers()).rejects.toThrow('pagination did not advance')
    expect(list).toHaveBeenCalledTimes(2)
  })
})
