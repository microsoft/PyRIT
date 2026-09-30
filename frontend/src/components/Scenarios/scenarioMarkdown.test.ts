import { normalizeScenarioMarkdown } from './scenarioMarkdown'

describe('normalizeScenarioMarkdown', () => {
  it('normalizes only double-backtick prose literals without rebuilding whitespace', () => {
    const source = [
      'Jailbreak details',
      '',
      'Set ``num_jailbreaks`` before launch.',
      '',
      '````text',
      'Keep ``literal fence text`` unchanged.',
      '````',
      '',
      '    Keep ``indented code`` unchanged.',
    ].join('\r\n')

    expect(normalizeScenarioMarkdown(source)).toBe([
      'Jailbreak details',
      '',
      'Set `num_jailbreaks` before launch.',
      '',
      '````text',
      'Keep ``literal fence text`` unchanged.',
      '````',
      '',
      '    Keep ``indented code`` unchanged.',
    ].join('\r\n'))
  })

  it('preserves escaped literals and double backticks nested in existing code spans', () => {
    const source = [
      String.raw`Keep \`\`escaped\`\` unchanged.`,
      'Keep ```outer ``literal`` span``` unchanged.',
      'Keep ``a `nested` code span`` unchanged.',
    ].join('\n')

    expect(normalizeScenarioMarkdown(source)).toBe(source)
  })

  it('leaves unmatched delimiters unchanged', () => {
    expect(normalizeScenarioMarkdown('Keep ``open intact.')).toBe('Keep ``open intact.')
  })

  it('preserves content inside an unclosed tilde fence', () => {
    const source = [
      '~~~text',
      'Keep ``literal fence text`` unchanged.',
      '```',
    ].join('\n')

    expect(normalizeScenarioMarkdown(source)).toBe(source)
  })
})
