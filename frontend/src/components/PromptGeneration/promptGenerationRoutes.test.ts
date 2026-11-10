import {
  newGenerationParamsFromSearch,
  newPromptGenerationRoutePath,
  promptGenerationRoutePath,
} from './promptGenerationRoutes'

describe('promptGenerationRoutes', () => {
  it('encodes the generation ID as one path segment', () => {
    expect(promptGenerationRoutePath('a/b')).toBe('/datasets/generate/a%2Fb')
  })

  it('builds the new-generation path with only the given parameters', () => {
    expect(newPromptGenerationRoutePath()).toBe('/datasets/generate/new')
    expect(newPromptGenerationRoutePath({ generator: 'assert', template: 'sycophancy' }))
      .toBe('/datasets/generate/new?generator=assert&template=sycophancy')
    expect(newPromptGenerationRoutePath({ suite: 'travel', from: 'gen-1', selectionKey: 'dataset:named:a&b' }))
      .toBe('/datasets/generate/new?suite=travel&from=gen-1&selection_key=dataset%3Anamed%3Aa%26b')
  })

  it('reads single parameters and ignores repeated or blank ones', () => {
    expect(newGenerationParamsFromSearch(new URLSearchParams(
      'generator=assert&template=a&template=b&suite=%20&from=gen-1&selection_key=dataset:named:x',
    ))).toEqual({
      generator: 'assert',
      template: undefined,
      suite: undefined,
      from: 'gen-1',
      selectionKey: 'dataset:named:x',
    })
  })
})
