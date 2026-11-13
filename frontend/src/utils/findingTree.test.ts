import {
  findingChildren, findingDescendantIds, findingPathName, flattenFindingTree,
} from './findingTree'

const AT = (minute: number): string => `2026-11-06T12:${String(minute).padStart(2, '0')}:00Z`
const ROOT = { id: 'root', parent_finding_id: null, created_at: AT(0) }
const FIRST = { id: 'first', parent_finding_id: 'root', created_at: AT(1) }
const NESTED = { id: 'nested', parent_finding_id: 'first', created_at: AT(2) }
const SECOND = { id: 'second', parent_finding_id: 'root', created_at: AT(3) }
const OTHER = { id: 'other', created_at: AT(4) }
const ORPHAN = { id: 'orphan', parent_finding_id: 'unlisted', created_at: AT(0) }

describe('findingTree', () => {
  it('keeps roots in listing order, lists sub-findings oldest first, and puts parents first', () => {
    const rows = flattenFindingTree([SECOND, ROOT, OTHER, NESTED, FIRST, ORPHAN])
    expect(rows.map(({ finding, depth }) => [finding.id, depth])).toEqual([
      ['root', 0], ['first', 1], ['nested', 2], ['second', 1], ['other', 0], ['orphan', 0],
    ])
  })

  it('breaks a tie in creation time by ID', () => {
    const later = { id: 'b', parent_finding_id: 'root', created_at: AT(1) }
    const earlier = { id: 'a', parent_finding_id: 'root', created_at: AT(1) }
    expect(findingChildren([ROOT, later, earlier]).get('root')?.map(finding => finding.id)).toEqual(['a', 'b'])
  })

  it('preserves microseconds before breaking creation-time ties', () => {
    const earlier = { id: 'b', parent_finding_id: 'root', created_at: '2026-11-06T12:00:00.000100Z' }
    const later = { id: 'a', parent_finding_id: 'root', created_at: '2026-11-06T12:00:00.000900+00:00' }
    expect(findingChildren([ROOT, later, earlier]).get('root')?.map(finding => finding.id)).toEqual(['b', 'a'])
  })

  it('treats a finding whose parent is not listed as a root', () => {
    const children = findingChildren([FIRST, NESTED])
    expect(children.get(null)?.map(finding => finding.id)).toEqual(['first'])
    expect(children.get('first')?.map(finding => finding.id)).toEqual(['nested'])
  })

  it('finds every descendant of a finding', () => {
    const children = findingChildren([ROOT, FIRST, NESTED, SECOND, OTHER])
    expect(findingDescendantIds(children, 'root').sort()).toEqual(['first', 'nested', 'second'])
    expect(findingDescendantIds(children, 'other')).toEqual([])
  })

  it('handles a decomposition deeper than the call stack', () => {
    const chain = Array.from({ length: 20_000 }, (_, index) => ({
      id: `node-${index}`, parent_finding_id: index === 0 ? null : `node-${index - 1}`, created_at: AT(0),
    }))
    expect(flattenFindingTree(chain).at(-1)).toEqual({ finding: chain.at(-1), depth: 19_999 })
  })

  it('names an unnamed path by its position', () => {
    expect(findingPathName({ name: 'Credential theft' }, 0)).toBe('Credential theft')
    expect(findingPathName({ name: '  ' }, 1)).toBe('Path 2')
    expect(findingPathName({ name: null }, 2)).toBe('Path 3')
  })
})
