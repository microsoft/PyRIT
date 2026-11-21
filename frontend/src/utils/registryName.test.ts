import { suggestRegistryName, validateRegistryName } from './registryName'

describe('validateRegistryName', () => {
  it.each(['a', 'contoso_gpt4o', 'a'.repeat(64)])('should accept %s', (name) => {
    expect(validateRegistryName(name)).toBeNull()
  })

  it('should require a name', () => {
    expect(validateRegistryName('')).toBe('Name is required.')
  })

  it.each(['Contoso', 'a-b', '1abc', ' a', 'a'.repeat(65)])('should refuse %s', (name) => {
    expect(validateRegistryName(name)).toBe(
      'Use lowercase letters, digits and underscores, starting with a letter (max 64 characters).',
    )
  })
})

describe('suggestRegistryName', () => {
  it.each([
    ['OpenAI chat', 'openai_chat'],
    ['Weighted round robin', 'weighted_round_robin'],
    ['AzureBlobStorage', 'azure_blob_storage'],
    ['PromptShield', 'prompt_shield'],
    ['A2A', 'a2a'],
    ['', 'entry'],
    ['2fast', 'entry_2fast'],
  ])('should suggest a valid name from %p', (label, expected) => {
    expect(suggestRegistryName(label, new Set())).toBe(expected)
  })

  it.each([
    ['OpenAI chat', 'gpt-4o', 'openai_chat_gpt_4o'],
    ['Azure Machine Learning chat', 'Llama-3.2-3B-Instruct', 'azure_machine_learning_chat_llama_3_2_3b_instruct'],
    ['Realtime', 'gpt-realtime', 'realtime_gpt_realtime'],
    ['OpenAI chat', '', 'openai_chat'],
  ])('should add a detail such as the model to %p', (label, detail, expected) => {
    expect(suggestRegistryName(label, new Set(), detail)).toBe(expected)
  })

  it('should add a number until the name is free', () => {
    expect(suggestRegistryName('OpenAI chat', new Set(['openai_chat', 'openai_chat_2']))).toBe('openai_chat_3')
  })

  it('should stay within the length limit when it adds a number', () => {
    const suggestion = suggestRegistryName('x'.repeat(70), new Set(['x'.repeat(64)]))

    expect(suggestion).toBe(`${'x'.repeat(62)}_2`)
    expect(validateRegistryName(suggestion)).toBeNull()
  })
})
