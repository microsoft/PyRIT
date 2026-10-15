import {
  clearedSeedFilters,
  DEFAULT_SEED_PAGE_SIZE,
  popSeedCursor,
  pushSeedCursor,
  seedBrowserQueryFromSearchParams,
  seedBrowserSearchParams,
  isKnownRuntimeGeneration,
  runtimeGenerationChanged,
  seedCursorIsStale,
  seedCursorsFromNavigationState,
  seedFiltersActive,
  seedNavigationState,
  seedPageNumber,
  seedQueryWithoutPaging,
} from './seedExampleQuery'

const KEY = 'dataset:named:harmbench'

describe('seedBrowserQueryFromSearchParams', () => {
  it('reads seed search and facets and ignores catalog name search, unlabeled, and sort', () => {
    const params = new URLSearchParams()
    params.set('q', 'policy')
    params.set('unlabeled', 'true')
    params.set('sort', 'name_desc')
    params.set('search', 'password')
    params.append('modality', 'text')
    params.append('modality', 'image_path')
    params.append('seed_type', 'simulated_conversation')
    params.append('seed_type', 'not-a-type')
    params.append('harm_category', 'hate')
    params.set('limit', '50')
    params.set('cursor', 'cursor-2')
    params.set('example', 'example-1')

    expect(seedBrowserQueryFromSearchParams(params)).toEqual({
      search: 'password',
      modalities: ['text', 'image_path'],
      seedTypes: ['simulated_conversation'],
      harmCategories: ['hate'],
      limit: 50,
      cursor: 'cursor-2',
      exampleId: 'example-1',
      cursorRepeated: false,
      exampleRepeated: false,
    })
  })

  it('does not pick one value when the cursor, example, search, or limit is repeated', () => {
    const params = new URLSearchParams(
      'search=one&search=two&limit=10&limit=30&cursor=a&cursor=b&example=1&example=2',
    )
    const query = seedBrowserQueryFromSearchParams(params)

    expect(query.search).toBe('')
    expect(query.limit).toBe(DEFAULT_SEED_PAGE_SIZE)
    expect(query.cursor).toBeNull()
    expect(query.exampleId).toBeNull()
    expect(query.cursorRepeated).toBe(true)
    expect(query.exampleRepeated).toBe(true)
  })

  it('caps search at 1,000 characters and rejects a limit outside 1 to 100', () => {
    const params = new URLSearchParams()
    params.set('search', 'x'.repeat(1001))
    params.set('limit', '101')

    const query = seedBrowserQueryFromSearchParams(params)
    expect(query.search).toHaveLength(1000)
    expect(query.limit).toBe(DEFAULT_SEED_PAGE_SIZE)
  })
})

describe('seedBrowserSearchParams', () => {
  it('writes the selection key and repeated singular facets without catalog controls', () => {
    const params = seedBrowserSearchParams(KEY, {
      search: 'password',
      modalities: ['text', 'image_path'],
      seedTypes: ['prompt', 'simulated_conversation'],
      harmCategories: ['hate'],
      limit: 50,
      cursor: 'cursor-2',
      exampleId: 'example-1',
      cursorRepeated: false,
      exampleRepeated: false,
    })

    expect(params.get('selection_key')).toBe(KEY)
    expect(params.get('search')).toBe('password')
    expect(params.getAll('modality')).toEqual(['text', 'image_path'])
    expect(params.getAll('seed_type')).toEqual(['prompt', 'simulated_conversation'])
    expect(params.getAll('harm_category')).toEqual(['hate'])
    expect(params.get('limit')).toBe('50')
    expect(params.get('cursor')).toBe('cursor-2')
    expect(params.get('example')).toBe('example-1')
    expect(params.get('q')).toBeNull()
    expect(params.get('sort')).toBeNull()
    expect(params.get('unlabeled')).toBeNull()
  })

  it('omits default paging and does not emit a repeated cursor', () => {
    const params = seedBrowserSearchParams(KEY, {
      search: '',
      modalities: [],
      seedTypes: [],
      harmCategories: [],
      limit: DEFAULT_SEED_PAGE_SIZE,
      cursor: 'cursor-2',
      exampleId: null,
      cursorRepeated: true,
      exampleRepeated: false,
    })

    expect(Array.from(params.keys())).toEqual(['selection_key'])
  })
})

describe('seed cursor history', () => {
  it('pushes the current cursor and pops it without using the response prev_cursor', () => {
    const pageTwo = pushSeedCursor([], null)
    const pageThree = pushSeedCursor(pageTwo, 'cursor-2')
    expect(popSeedCursor(pageThree)).toEqual({ cursor: 'cursor-2', seedCursors: [null] })
    expect(popSeedCursor(pageTwo)).toEqual({ cursor: null, seedCursors: [] })
    expect(popSeedCursor([])).toBeNull()
    expect(seedPageNumber(
      { search: '', modalities: [], seedTypes: [], harmCategories: [], limit: 20, cursor: 'cursor-2', exampleId: null, cursorRepeated: false, exampleRepeated: false },
      pageTwo,
    )).toBe(2)
    expect(seedPageNumber(
      { search: '', modalities: [], seedTypes: [], harmCategories: [], limit: 20, cursor: 'cursor-2', exampleId: null, cursorRepeated: false, exampleRepeated: false },
      [],
    )).toBeNull()
  })

  it('drops an invalid cursor stack and keeps a validated catalog return path', () => {
    expect(seedCursorsFromNavigationState({ seedCursors: [null, 'cursor-2'] })).toEqual([null, 'cursor-2'])
    expect(seedCursorsFromNavigationState({ seedCursors: ['cursor-2', 4] })).toEqual([])
    expect(seedCursorsFromNavigationState(null)).toEqual([])

    const state = seedNavigationState(
      { catalogPath: '/datasets?q=policy&modality=text&sort=logical_examples_desc' },
      [null],
      'gen-1',
    )
    expect(state.catalogPath).toBe('/datasets?q=policy&modality=text&sort=logical_examples_desc')
    expect(state.seedCursors).toEqual([null])
    expect(seedCursorIsStale(state, 'gen-1')).toBe(false)
    expect(seedCursorIsStale(state, 'gen-2')).toBe(true)
    expect(seedCursorIsStale({ catalogPath: '/datasets' }, 'gen-2')).toBe(false)
    expect(seedCursorIsStale(state, '')).toBe(false)
    expect(seedCursorIsStale(state, 'gen-2', false)).toBe(false)
    expect(seedCursorIsStale({ ...state, cursorGeneration: '' }, 'gen-1')).toBe(false)
    expect(isKnownRuntimeGeneration({ generation: '', ready: true })).toBe(false)
    expect(isKnownRuntimeGeneration({ generation: 'gen-1', ready: false })).toBe(false)
    expect(isKnownRuntimeGeneration({ generation: 'gen-1', ready: true })).toBe(true)
    expect(runtimeGenerationChanged(
      { generation: '', ready: false },
      { generation: 'gen-1', ready: true },
    )).toBe(false)
    expect(runtimeGenerationChanged(
      { generation: 'gen-1', ready: true },
      { generation: 'gen-1', ready: false },
    )).toBe(false)
    expect(runtimeGenerationChanged(
      { generation: 'gen-1', ready: true },
      { generation: 'gen-2', ready: true },
    )).toBe(true)
    expect(seedQueryWithoutPaging({
      search: 'password',
      modalities: ['text'],
      seedTypes: ['prompt'],
      harmCategories: [],
      limit: 50,
      cursor: 'cursor-2',
      exampleId: 'example-1',
      cursorRepeated: true,
      exampleRepeated: true,
    })).toEqual({
      search: 'password',
      modalities: ['text'],
      seedTypes: ['prompt'],
      harmCategories: [],
      limit: 50,
      cursor: null,
      exampleId: null,
      cursorRepeated: false,
      exampleRepeated: false,
    })
    expect(seedNavigationState({ catalogPath: 'https://evil.example/datasets' }, [], 'gen-1').catalogPath).toBe('/datasets')
  })

  it('clears search and facets but keeps the inspected example and page size', () => {
    const cleared = clearedSeedFilters({
      search: 'password',
      modalities: ['text'],
      seedTypes: ['prompt'],
      harmCategories: ['hate'],
      limit: 50,
      cursor: 'cursor-2',
      exampleId: 'example-1',
      cursorRepeated: true,
      exampleRepeated: false,
    })
    expect(seedFiltersActive(cleared)).toBe(false)
    expect(cleared.cursor).toBeNull()
    expect(cleared.exampleId).toBe('example-1')
    expect(cleared.limit).toBe(50)
  })
})
