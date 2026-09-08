interface MarkdownFence {
  marker: '`' | '~'
  length: number
}

function countRun(value: string, start: number, marker: string): number {
  let end = start
  while (value[end] === marker) {
    end += 1
  }
  return end - start
}

function isEscaped(value: string, index: number): boolean {
  let slashCount = 0
  for (let cursor = index - 1; cursor >= 0 && value[cursor] === '\\'; cursor -= 1) {
    slashCount += 1
  }
  return slashCount % 2 === 1
}

function findClosingBackticks(value: string, start: number, delimiterLength: number): number {
  let cursor = start
  while (cursor < value.length) {
    if (value[cursor] !== '`') {
      cursor += 1
      continue
    }
    const runLength = countRun(value, cursor, '`')
    if (!isEscaped(value, cursor) && runLength === delimiterLength) {
      return cursor
    }
    cursor += runLength
  }
  return -1
}

function normalizeProseLine(line: string): string {
  const output: string[] = []
  let cursor = 0

  while (cursor < line.length) {
    if (line[cursor] !== '`' || isEscaped(line, cursor)) {
      output.push(line[cursor])
      cursor += 1
      continue
    }

    const delimiterLength = countRun(line, cursor, '`')
    const closingIndex = findClosingBackticks(
      line,
      cursor + delimiterLength,
      delimiterLength,
    )
    if (closingIndex < 0) {
      output.push(line.slice(cursor, cursor + delimiterLength))
      cursor += delimiterLength
      continue
    }

    const closingEnd = closingIndex + delimiterLength
    const literal = line.slice(cursor + delimiterLength, closingIndex)
    const isNarrowMystLiteral =
      delimiterLength === 2
      && literal.length > 0
      && literal === literal.trim()
      && !literal.includes('`')
    output.push(
      isNarrowMystLiteral
        ? `\`${literal}\``
        : line.slice(cursor, closingEnd),
    )
    cursor = closingEnd
  }

  return output.join('')
}

function openingFence(line: string): MarkdownFence | null {
  const match = /^ {0,3}(`{3,}|~{3,})/.exec(line)
  if (!match) {
    return null
  }
  const run = match[1]
  return {
    marker: run[0] === '`' ? '`' : '~',
    length: run.length,
  }
}

function closesFence(line: string, fence: MarkdownFence): boolean {
  const indentLength = /^ {0,3}/.exec(line)?.[0].length ?? 0
  if (line[indentLength] !== fence.marker) {
    return false
  }
  const runLength = countRun(line, indentLength, fence.marker)
  return runLength >= fence.length && line.slice(indentLength + runLength).trim().length === 0
}

/**
 * Converts narrow MyST double-backtick literals in prose to CommonMark code
 * spans while preserving source whitespace and every existing code context.
 */
export function normalizeScenarioMarkdown(content: string): string {
  let fence: MarkdownFence | null = null

  return content.replace(/[^\r\n]*(?:\r\n|\r|\n|$)/g, (line: string) => {
    if (line.length === 0) {
      return line
    }
    const endingMatch = /(\r\n|\r|\n)$/.exec(line)
    const ending = endingMatch?.[0] ?? ''
    const body = ending ? line.slice(0, -ending.length) : line

    if (fence) {
      if (closesFence(body, fence)) {
        fence = null
      }
      return line
    }

    const nextFence = openingFence(body)
    if (nextFence) {
      fence = nextFence
      return line
    }
    if (/^(?: {4}|\t)/.test(body)) {
      return line
    }
    return `${normalizeProseLine(body)}${ending}`
  })
}
