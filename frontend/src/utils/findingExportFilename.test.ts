import { findingExportFilename } from './findingExportFilename'

const OPERATION_ID = 'c1e5502b-91f6-4d65-b182-4b028c7a75f6'
// Built from local parts, so the expected time does not depend on the test machine's time zone.
const EXPORT_TIME = new Date(2026, 9, 22, 12, 58, 33)

describe('findingExportFilename', () => {
  it.each([
    ['Synthetic evaluation', 'synthetic-evaluation'],
    ['Q3 Red Team / Copilot (v2)', 'q3-red-team-copilot-v2'],
    ['Évaluation: modèle 2', 'évaluation-modèle-2'],
    ['E\u0301valuation', 'évaluation'],
    // Devanagari writes vowel signs and viramas as combining marks, which belong to their word.
    ['हिन्दी परीक्षण', 'हिन्दी-परीक्षण'],
    ['snake_case name', 'snake-case-name'],
    [`${'a'.repeat(58)} bcdef`, 'a'.repeat(58)],
    ['x'.repeat(80), 'x'.repeat(60)],
    ['𠀀'.repeat(61), '𠀀'.repeat(60)],
    // The 61st code point is a vowel sign, so the cut moves back before its letter.
    [`क${'कि'.repeat(30)}`, `क${'कि'.repeat(29)}`],
    ['???', OPERATION_ID],
  ])('names %j after the Operation, in local time', (name, expected) => {
    expect(findingExportFilename({ id: OPERATION_ID, name }, 'docx', EXPORT_TIME))
      .toBe(`copyrit-findings-${expected}-2026-10-22-1258.docx`)
  })

  it('pads single-digit months, days, hours and minutes', () => {
    expect(findingExportFilename({ id: OPERATION_ID, name: 'Synthetic evaluation' }, 'md', new Date(2026, 0, 5, 7, 4)))
      .toBe('copyrit-findings-synthetic-evaluation-2026-01-05-0704.md')
  })
})
