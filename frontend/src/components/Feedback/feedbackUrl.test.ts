import { buildGithubFeedbackUrl, type FeedbackInput } from './feedbackUrl'

function parse(url: string): { base: string; params: URLSearchParams } {
  const idx = url.indexOf('?')
  return {
    base: url.slice(0, idx),
    params: new URLSearchParams(url.slice(idx + 1)),
  }
}

describe('buildGithubFeedbackUrl', () => {
  describe('shared behaviour', () => {
    it('targets the PyRIT issues/new endpoint', () => {
      const { base } = parse(
        buildGithubFeedbackUrl({ category: 'praise', body: 'Loving it!' }),
      )
      // Accept either casing — github.com/Microsoft/PyRIT redirects to
      // github.com/microsoft/PyRIT, so the dialog works correctly with either.
      expect(base).toMatch(
        /^https:\/\/github\.com\/(microsoft|Microsoft)\/PyRIT\/issues\/new$/,
      )
    })

    it('always attaches the GUI umbrella label', () => {
      const inputs: FeedbackInput[] = [
        { category: 'bug', describe: 'thing broke' },
        { category: 'feature', solution: 'add a button' },
        { category: 'doc', issue: 'README typo' },
        { category: 'praise', body: 'thanks!' },
        { category: 'other', body: 'just an FYI' },
      ]
      for (const input of inputs) {
        const { params } = parse(buildGithubFeedbackUrl(input))
        const labels = (params.get('labels') ?? '').split(',')
        expect(labels[0]).toBe('GUI')
      }
    })

    it('URL-encodes special characters in body and contact', () => {
      const url = buildGithubFeedbackUrl({
        category: 'bug',
        describe: 'Issue: 100% reproducible & breaks <script>alert(1)</script>',
        optional_contact: 'a+b@example.com',
      })
      expect(url).toContain('%3Cscript%3E')
      expect(url).toContain('%26')
      expect(url).toContain('a%2Bb%40example.com')
    })

    it('truncates long titles to ~80 characters with an ellipsis', () => {
      const longLine = 'a'.repeat(200)
      const { params } = parse(
        buildGithubFeedbackUrl({ category: 'praise', body: longLine }),
      )
      const title = params.get('title') ?? ''
      expect(title.length).toBeLessThanOrEqual(80)
      expect(title.endsWith('…')).toBe(true)
      expect(title.startsWith('[Co-PyRIT Praise] ')).toBe(true)
    })

    it('omits the contact section when contact is blank or missing', () => {
      const { params } = parse(
        buildGithubFeedbackUrl({ category: 'praise', body: 'so good' }),
      )
      const body = params.get('body') ?? ''
      expect(body).not.toContain('#### Preferred contact')
    })

    it('includes the contact section when provided', () => {
      const { params } = parse(
        buildGithubFeedbackUrl({
          category: 'praise',
          body: 'great work',
          optional_contact: 'alice@contoso.com',
        }),
      )
      const body = params.get('body') ?? ''
      expect(body).toContain('#### Preferred contact')
      expect(body).toContain('alice@contoso.com')
    })

    it('appends a context footer when context fields are provided', () => {
      const { params } = parse(
        buildGithubFeedbackUrl({
          category: 'other',
          body: 'just thoughts',
          context: {
            app_version: '1.2.3',
            current_view: 'chat',
            target_type: 'OpenAIChatTarget',
          },
        }),
      )
      const body = params.get('body') ?? ''
      expect(body).toContain('Submitted via Co-PyRIT in-app feedback.')
      expect(body).toContain('App version: 1.2.3')
      expect(body).toContain('View: chat')
      expect(body).toContain('Target type: OpenAIChatTarget')
    })

    it('omits the context footer when context is undefined or empty', () => {
      const { params: a } = parse(
        buildGithubFeedbackUrl({ category: 'praise', body: 'fab' }),
      )
      expect(a.get('body') ?? '').not.toContain('Submitted via Co-PyRIT')

      const { params: b } = parse(
        buildGithubFeedbackUrl({ category: 'praise', body: 'fab', context: {} }),
      )
      expect(b.get('body') ?? '').not.toContain('Submitted via Co-PyRIT')
    })
  })

  describe('bug category', () => {
    const bug: FeedbackInput = {
      category: 'bug',
      describe: 'Chat window crashes when sending an empty message.',
      repro: '1. Open chat\n2. Press send with empty input',
      expected: 'Nothing should happen.',
      actual: 'App crashes with TypeError.',
      versions: 'PyRIT 0.5.0, Windows 11',
    }

    it('selects the bug_report template and adds the bug label', () => {
      const { params } = parse(buildGithubFeedbackUrl(bug))
      expect(params.get('template')).toBe('bug_report.md')
      expect(params.get('labels')).toBe('GUI,bug')
    })

    it('builds a title with the [Co-PyRIT Bug] tag and Describe excerpt', () => {
      const { params } = parse(buildGithubFeedbackUrl(bug))
      expect(params.get('title')).toBe(
        '[Co-PyRIT Bug] Chat window crashes when sending an empty message.',
      )
    })

    it('emits the bug template sections in order', () => {
      const { params } = parse(buildGithubFeedbackUrl(bug))
      const body = params.get('body') ?? ''
      const idx = (h: string) => body.indexOf(`#### ${h}`)
      expect(idx('Describe the bug')).toBeGreaterThanOrEqual(0)
      expect(idx('Steps/Code to Reproduce')).toBeGreaterThan(idx('Describe the bug'))
      expect(idx('Expected Results')).toBeGreaterThan(idx('Steps/Code to Reproduce'))
      expect(idx('Actual Results')).toBeGreaterThan(idx('Expected Results'))
      expect(idx('Versions')).toBeGreaterThan(idx('Actual Results'))
    })

    it('hints to users that screenshots are pasted on github.com', () => {
      const { params } = parse(buildGithubFeedbackUrl(bug))
      expect(params.get('body') ?? '').toContain('drag image files into this textarea on GitHub')
    })

    it('omits empty optional sections', () => {
      const { params } = parse(
        buildGithubFeedbackUrl({ category: 'bug', describe: 'thing broke' }),
      )
      const body = params.get('body') ?? ''
      expect(body).toContain('#### Describe the bug')
      expect(body).not.toContain('#### Steps/Code to Reproduce')
      expect(body).not.toContain('#### Versions')
    })
  })

  describe('feature category', () => {
    const feature: FeedbackInput = {
      category: 'feature',
      problem: 'I keep losing my conversation history.',
      solution: 'Persist chat history across restarts.',
      alternatives: 'Manual export button.',
      additional_context: 'See attached screenshots.',
    }

    it('selects the feature_request template and adds the enhancement label', () => {
      const { params } = parse(buildGithubFeedbackUrl(feature))
      expect(params.get('template')).toBe('feature_request.md')
      expect(params.get('labels')).toBe('GUI,enhancement')
    })

    it('uses the Solution as the title excerpt', () => {
      const { params } = parse(buildGithubFeedbackUrl(feature))
      expect(params.get('title')).toBe(
        '[Co-PyRIT Feature request] Persist chat history across restarts.',
      )
    })

    it('emits the feature template sections', () => {
      const { params } = parse(buildGithubFeedbackUrl(feature))
      const body = params.get('body') ?? ''
      expect(body).toContain(
        '#### Is your feature request related to a problem? Please describe.',
      )
      expect(body).toContain("#### Describe the solution you'd like")
      expect(body).toContain("#### Describe alternatives you've considered, if relevant")
      expect(body).toContain('#### Additional context')
    })
  })

  describe('doc category', () => {
    const doc: FeedbackInput = {
      category: 'doc',
      issue: 'The Quickstart references an old install command.',
      suggestion: 'Replace pip install with uv pip install.',
    }

    it('selects the doc_improvement template and adds Documentation label', () => {
      const { params } = parse(buildGithubFeedbackUrl(doc))
      expect(params.get('template')).toBe('doc_improvement.md')
      expect(params.get('labels')).toBe('GUI,documentation')
    })

    it('emits the doc template sections', () => {
      const { params } = parse(buildGithubFeedbackUrl(doc))
      const body = params.get('body') ?? ''
      expect(body).toContain('#### Describe the issue linked to the documentation')
      expect(body).toContain('#### Suggest a potential alternative/fix')
    })
  })

  describe('praise category', () => {
    const praise: FeedbackInput = {
      category: 'praise',
      body: 'Co-PyRIT cut my red-team setup time in half — thank you!',
    }

    it('selects the praise template and adds praise label', () => {
      const { params } = parse(buildGithubFeedbackUrl(praise))
      expect(params.get('template')).toBe('praise.md')
      expect(params.get('labels')).toBe('GUI,praise')
    })

    it('emits the praise "What do you love?" section', () => {
      const { params } = parse(buildGithubFeedbackUrl(praise))
      expect(params.get('body') ?? '').toContain('#### What do you love?')
    })

    it('builds a title with the [Co-PyRIT Praise] tag', () => {
      const { params } = parse(buildGithubFeedbackUrl(praise))
      expect(params.get('title')).toBe(
        '[Co-PyRIT Praise] Co-PyRIT cut my red-team setup time in half — thank you!',
      )
    })
  })

  describe('other category', () => {
    const other: FeedbackInput = {
      category: 'other',
      body: 'Random thought: could you support dark mode?',
    }

    it('selects the blank_template and adds only the GUI label', () => {
      const { params } = parse(buildGithubFeedbackUrl(other))
      expect(params.get('template')).toBe('blank_template.md')
      expect(params.get('labels')).toBe('GUI')
    })

    it('emits a generic Feedback section', () => {
      const { params } = parse(buildGithubFeedbackUrl(other))
      expect(params.get('body') ?? '').toContain('#### Feedback')
    })
  })
})
