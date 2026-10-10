import { memo } from 'react'

import { mergeClasses } from '@fluentui/react-components'
import Markdown from 'react-markdown'
import type { Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { useMarkdownContentStyles } from './MarkdownContent.styles'

interface MarkdownContentProps {
  /** Raw Markdown source. Treated as UNTRUSTED — may be model-generated. */
  content: string
  /** Optional test id applied to the wrapper element. */
  testId?: string
  /** Optional themed class for the surface embedding the shared renderer. */
  className?: string
}

// Render every link in a new tab. `rel="noopener noreferrer"` prevents the
// opened page from reaching back through `window.opener` (reverse tabnabbing).
// We do not spread arbitrary props onto the anchor so no unexpected attributes
// from the parsed source can leak through.
//
// Inline images (`![alt](url)`) are rendered as a click-through LINK rather than
// an auto-loading image element. Because the content is untrusted (model-generated),
// auto-loading would fetch a model-controlled URL on render — a tracking-pixel /
// internal-probe vector that silently leaks the operator's IP, a view timestamp,
// and any query-encoded data. A link preserves the operator's ability to open
// the image deliberately. (The `src`/`href` is already URL-sanitized by
// react-markdown, so `javascript:` and other dangerous URIs are stripped.)
const MARKDOWN_COMPONENTS: Components = {
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  ),
  img: ({ src, alt }) => {
    const href = typeof src === 'string' ? src : undefined
    return (
      <a href={href} target="_blank" rel="noopener noreferrer">
        {alt || href}
      </a>
    )
  },
}

// Hoisted so the same array identity is reused across renders.
const REMARK_PLUGINS = [remarkGfm]

/**
 * Renders untrusted Markdown safely.
 *
 * `react-markdown` builds a React element tree (it never uses
 * `dangerouslySetInnerHTML`) and escapes any embedded raw HTML by default — we
 * deliberately do NOT add the `rehype-raw` plugin, which would re-enable it.
 * Combined with react-markdown's default URL sanitization (which strips
 * `javascript:` and other dangerous URIs), this is safe for adversarial,
 * model-generated content.
 *
 * Memoized because Markdown parsing is comparatively expensive and message
 * content is stable across the frequent re-renders of the message list.
 */
function MarkdownContent({ content, testId, className }: MarkdownContentProps) {
  const styles = useMarkdownContentStyles()

  return (
    <div className={mergeClasses(styles.root, className)} data-testid={testId}>
      <Markdown remarkPlugins={REMARK_PLUGINS} components={MARKDOWN_COMPONENTS}>
        {content}
      </Markdown>
    </div>
  )
}

export default memo(MarkdownContent)
