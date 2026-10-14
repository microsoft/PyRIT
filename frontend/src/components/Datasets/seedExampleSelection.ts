/**
 * The one logical example chosen for a later chat import.
 * Multi-select is a later milestone and must not concatenate independent examples.
 */
export interface SeedExampleChoice {
  readonly selectionKey: string
  readonly exampleId: string
}

export function singleSeedExampleChoice(selectionKey: string, exampleId: string): SeedExampleChoice {
  return { selectionKey, exampleId }
}
