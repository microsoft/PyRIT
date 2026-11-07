import {
  findingChildren, findingDescendantIds, findingPathName, flattenFindingTree, isFindingPathComplete,
} from './findingTree'

const ROOT = { id: 'root', parent_finding_id: null }
const FIRST = { id: 'first', parent_finding_id: 'root' }
const NESTED = { id: 'nested', parent_finding_id: 'first' }
const SECOND = { id: 'second', parent_finding_id: 'root' }
const OTHER = { id: 'other' }
const ORPHAN = { id: 'orphan', parent_finding_id: 'unlisted' }

describe('findingTree', () => {
  it('lists parents before sub-findings and keeps sibling order', () => {
    const rows = flattenFindingTree([SECOND, ROOT, OTHER, NESTED, FIRST, ORPHAN])
    expect(rows.map(({ finding, depth }) => [finding.id, depth])).toEqual([
      ['root', 0], ['second', 1], ['first', 1], ['nested', 2], ['other', 0], ['orphan', 0],
    ])
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
      id: `node-${index}`, parent_finding_id: index === 0 ? null : `node-${index - 1}`,
    }))
    expect(flattenFindingTree(chain).at(-1)).toEqual({ finding: chain.at(-1), depth: 19_999 })
  })

  it('completes a path only when every step was demonstrated', () => {
    const demonstrated = new Map([['a', true], ['b', false]])
    expect(isFindingPathComplete({ steps: ['a'] }, demonstrated)).toBe(true)
    expect(isFindingPathComplete({ steps: ['a', 'b'] }, demonstrated)).toBe(false)
    expect(isFindingPathComplete({ steps: ['a', 'missing'] }, demonstrated)).toBe(false)
    expect(isFindingPathComplete({ steps: [] }, demonstrated)).toBe(false)
  })

  it('names an unnamed path by its position', () => {
    expect(findingPathName({ name: 'Credential theft' }, 0)).toBe('Credential theft')
    expect(findingPathName({ name: '  ' }, 1)).toBe('Path 2')
    expect(findingPathName({ name: null }, 2)).toBe('Path 3')
  })
})
