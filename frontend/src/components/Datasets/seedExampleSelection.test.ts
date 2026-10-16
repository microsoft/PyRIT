import { singleSeedExampleChoice } from './seedExampleSelection'

describe('singleSeedExampleChoice', () => {
  it('keeps one dataset key and one logical example id', () => {
    expect(singleSeedExampleChoice('dataset:unnamed', 'example-1')).toEqual({
      selectionKey: 'dataset:unnamed',
      exampleId: 'example-1',
    })
  })
})
