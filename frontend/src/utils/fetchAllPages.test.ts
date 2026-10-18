import { fetchAllPages } from './fetchAllPages'

describe('fetchAllPages', () => {
  it('returns all items from a single page', async () => {
    const fetchPage = jest.fn().mockResolvedValue({
      items: [1, 2, 3],
      pagination: { has_more: false },
    })

    const items = await fetchAllPages(fetchPage)

    expect(items).toEqual([1, 2, 3])
    expect(fetchPage).toHaveBeenCalledTimes(1)
    expect(fetchPage).toHaveBeenCalledWith(undefined)
  })

  it('follows next_cursor across multiple pages', async () => {
    const fetchPage = jest
      .fn()
      .mockResolvedValueOnce({ items: [1], pagination: { has_more: true, next_cursor: 'c1' } })
      .mockResolvedValueOnce({ items: [2], pagination: { has_more: true, next_cursor: 'c2' } })
      .mockResolvedValueOnce({ items: [3], pagination: { has_more: false } })

    const items = await fetchAllPages(fetchPage)

    expect(items).toEqual([1, 2, 3])
    expect(fetchPage).toHaveBeenCalledTimes(3)
    expect(fetchPage).toHaveBeenNthCalledWith(2, 'c1')
    expect(fetchPage).toHaveBeenNthCalledWith(3, 'c2')
  })

  it('stops if the server repeats a cursor instead of looping forever', async () => {
    const fetchPage = jest.fn().mockResolvedValue({
      items: [1],
      pagination: { has_more: true, next_cursor: 'same' },
    })

    const items = await fetchAllPages(fetchPage, undefined, String)

    expect(items).toEqual([1])
    expect(fetchPage).toHaveBeenCalledTimes(2)
  })

  it('stops after maxPages even if has_more stays true with new cursors', async () => {
    const fetchPage = jest.fn().mockImplementation((cursor?: string) => {
      const next = cursor ? `${cursor}-x` : 'c1'
      return Promise.resolve({ items: [next], pagination: { has_more: true, next_cursor: next } })
    })

    const items = await fetchAllPages(fetchPage, 3)

    expect(items).toHaveLength(3)
    expect(fetchPage).toHaveBeenCalledTimes(3)
  })

  it('propagates a rejected page fetch', async () => {
    const fetchPage = jest.fn().mockRejectedValue(new Error('boom'))
    await expect(fetchAllPages(fetchPage)).rejects.toThrow('boom')
  })

  it.each([null, undefined, ''])('rejects a missing cursor in complete-list mode (%s)', async (cursor) => {
    const fetchPage = jest.fn().mockResolvedValue({
      items: [1], pagination: { has_more: true, next_cursor: cursor },
    })
    await expect(fetchAllPages(fetchPage, undefined, undefined, true)).rejects.toThrow('invalid page cursor')
  })

  it('rejects repeated cursors in complete-list mode', async () => {
    const fetchPage = jest.fn().mockResolvedValue({
      items: [1], pagination: { has_more: true, next_cursor: 'same' },
    })
    await expect(fetchAllPages(fetchPage, undefined, undefined, true)).rejects.toThrow('invalid page cursor')
    expect(fetchPage).toHaveBeenCalledTimes(2)
  })

  it('rejects the page limit instead of returning an incomplete list', async () => {
    const fetchPage = jest.fn().mockResolvedValue({
      items: [1], pagination: { has_more: true, next_cursor: 'next' },
    })
    await expect(fetchAllPages(fetchPage, 1, undefined, true)).rejects.toThrow('page limit')
  })

  it('returns a complete list on the final allowed page and deduplicates keys', async () => {
    const fetchPage = jest.fn()
      .mockResolvedValueOnce({ items: [1, 2], pagination: { has_more: true, next_cursor: 'next' } })
      .mockResolvedValueOnce({ items: [2, 3], pagination: { has_more: false } })
    await expect(fetchAllPages(fetchPage, 2, String, true)).resolves.toEqual([1, 2, 3])
  })
})
