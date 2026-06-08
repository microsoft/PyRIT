/**
 * Builds a pre-filled GitHub "new issue" URL for the in-app feedback dialog.
 *
 * Each category in the dialog maps to one of the repo's real
 * `.github/ISSUE_TEMPLATE/*.md` files and contributes the same labels that
 * template would apply, plus a shared `GUI` umbrella label so the triage
 * workflow can find feedback issues filed via the app.
 *
 * Doing this client-side means we run no feedback service, hold no
 * credential, and inherit GitHub's auth/spam/abuse handling.
 */

const FEEDBACK_REPO_OWNER = 'microsoft'
const FEEDBACK_REPO_NAME = 'PyRIT'
const UMBRELLA_LABEL = 'GUI'
const TITLE_MAX_CHARS = 80

export type FeedbackCategory = 'bug' | 'feature' | 'doc' | 'praise' | 'other'

export interface FeedbackContext {
  app_version?: string
  current_view?: string
  target_type?: string
}

export interface FeedbackContactAndContext {
  optional_contact?: string
  context?: FeedbackContext
}

export interface BugFields {
  category: 'bug'
  describe: string
  repro?: string
  expected?: string
  actual?: string
  versions?: string
}

export interface FeatureFields {
  category: 'feature'
  problem?: string
  solution: string
  alternatives?: string
  additional_context?: string
}

export interface DocFields {
  category: 'doc'
  issue: string
  suggestion?: string
}

export interface PraiseFields {
  category: 'praise'
  body: string
}

export interface OtherFields {
  category: 'other'
  body: string
}

export type FeedbackFields =
  | BugFields
  | FeatureFields
  | DocFields
  | PraiseFields
  | OtherFields

export type FeedbackInput = FeedbackFields & FeedbackContactAndContext

interface CategoryMeta {
  /** Human-readable label shown in the dropdown and in the issue title tag. */
  label: string
  /** The exact `.github/ISSUE_TEMPLATE/*.md` filename to pre-select. */
  template: string
  /** Extra labels applied alongside the umbrella `GUI` label. Match the
   *  template's own front-matter `labels:` so issues filed via the dialog
   *  look identical to issues filed via the template chooser. */
  extra_labels: string[]
}

const CATEGORY_META: Record<FeedbackCategory, CategoryMeta> = {
  bug: {
    label: 'Bug',
    template: 'bug_report.md',
    extra_labels: ['bug'],
  },
  feature: {
    label: 'Feature request',
    template: 'feature_request.md',
    extra_labels: ['enhancement'],
  },
  doc: {
    label: 'Documentation improvement',
    template: 'doc_improvement.md',
    extra_labels: ['documentation'],
  },
  praise: {
    label: 'Praise',
    template: 'praise.md',
    extra_labels: ['praise'],
  },
  other: {
    label: 'Other',
    template: 'blank_template.md',
    extra_labels: [],
  },
}

/** Public so the dialog can render the dropdown labels from a single source. */
export function getCategoryLabel(category: FeedbackCategory): string {
  return CATEGORY_META[category].label
}

/**
 * Build the full https://github.com/.../issues/new?... URL with the user's
 * feedback pre-filled in title, body, and labels.
 *
 * The returned URL is safe to pass to `window.open(url, '_blank',
 * 'noopener,noreferrer')`. URL params are properly encoded by URLSearchParams.
 */
export function buildGithubFeedbackUrl(input: FeedbackInput): string {
  const meta = CATEGORY_META[input.category]
  const title = buildTitle(input, meta)
  const body = buildMarkdownBody(input)
  const labels = [UMBRELLA_LABEL, ...meta.extra_labels].join(',')

  const params = new URLSearchParams({
    template: meta.template,
    title,
    body,
    labels,
  })

  return `https://github.com/${FEEDBACK_REPO_OWNER}/${FEEDBACK_REPO_NAME}/issues/new?${params.toString()}`
}

function firstNonEmptyLine(...candidates: (string | undefined)[]): string {
  for (const c of candidates) {
    const line = (c ?? '').split('\n')[0]?.trim() ?? ''
    if (line) return line
  }
  return ''
}

function buildTitle(input: FeedbackInput, meta: CategoryMeta): string {
  const tag = `[Co-PyRIT ${meta.label}]`
  const excerpt = excerptForCategory(input)
  if (!excerpt) return tag
  const room = TITLE_MAX_CHARS - tag.length - 1
  if (excerpt.length <= room) return `${tag} ${excerpt}`
  const truncated = excerpt.slice(0, Math.max(0, room - 1)).trimEnd()
  return `${tag} ${truncated}…`
}

function excerptForCategory(input: FeedbackInput): string {
  switch (input.category) {
    case 'bug':
      return firstNonEmptyLine(input.describe, input.actual, input.repro)
    case 'feature':
      return firstNonEmptyLine(input.solution, input.problem)
    case 'doc':
      return firstNonEmptyLine(input.issue, input.suggestion)
    case 'praise':
    case 'other':
      return firstNonEmptyLine(input.body)
  }
}

function buildMarkdownBody(input: FeedbackInput): string {
  const sections: string[] = []
  switch (input.category) {
    case 'bug':
      sections.push(section('Describe the bug', input.describe))
      sections.push(section('Steps/Code to Reproduce', input.repro))
      sections.push(section('Expected Results', input.expected))
      sections.push(section('Actual Results', input.actual))
      sections.push(section('Versions', input.versions))
      sections.push(
        '<!-- Screenshots: drag image files into this textarea on GitHub to attach them. -->',
      )
      break
    case 'feature':
      sections.push(
        section(
          'Is your feature request related to a problem? Please describe.',
          input.problem,
        ),
      )
      sections.push(section("Describe the solution you'd like", input.solution))
      sections.push(
        section("Describe alternatives you've considered, if relevant", input.alternatives),
      )
      sections.push(section('Additional context', input.additional_context))
      break
    case 'doc':
      sections.push(section('Describe the issue linked to the documentation', input.issue))
      sections.push(section('Suggest a potential alternative/fix', input.suggestion))
      break
    case 'praise':
      sections.push(section('What do you love?', input.body))
      break
    case 'other':
      sections.push(section('Feedback', input.body))
      break
  }

  const contact = input.optional_contact?.trim()
  if (contact) {
    sections.push(section('Preferred contact', contact))
  }

  const contextBits = collectContextBits(input.context)
  if (contextBits.length > 0) {
    sections.push(
      [
        '---',
        '',
        '<sub>Submitted via Co-PyRIT in-app feedback.<br>',
        contextBits.join(' · '),
        '</sub>',
      ].join('\n'),
    )
  }

  return sections.filter((s) => s.length > 0).join('\n\n')
}

function section(heading: string, value: string | undefined): string {
  const trimmed = value?.trim()
  if (!trimmed) return ''
  return `#### ${heading}\n${trimmed}`
}

function collectContextBits(context: FeedbackContext | undefined): string[] {
  if (!context) return []
  const bits: string[] = []
  if (context.app_version) bits.push(`App version: ${context.app_version}`)
  if (context.current_view) bits.push(`View: ${context.current_view}`)
  if (context.target_type) bits.push(`Target type: ${context.target_type}`)
  return bits
}
