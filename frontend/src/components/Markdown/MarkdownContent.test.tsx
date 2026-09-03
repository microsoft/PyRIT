import React from 'react'
import { render, screen } from '@testing-library/react'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import MarkdownContent from './MarkdownContent'

function TestWrapper({ children }: { children: React.ReactNode }) {
  return <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
}

const RAW_IMAGE_HTML = ['<', 'img src=x onerror="alert(1)">'].join('')

describe('MarkdownContent', () => {
  it('renders bold text as a <strong> element', () => {
    render(
      <TestWrapper>
        <MarkdownContent content="Hello **world**" />
      </TestWrapper>,
    )
    const strong = screen.getByText('world')
    expect(strong.tagName).toBe('STRONG')
  })

  it('renders headings with the correct semantic level', () => {
    render(
      <TestWrapper>
        <MarkdownContent content="# Title" />
      </TestWrapper>,
    )
    expect(screen.getByRole('heading', { level: 1, name: 'Title' })).toBeInTheDocument()
  })

  it('renders GitHub-flavored tables via remark-gfm', () => {
    const table = ['| A | B |', '| - | - |', '| 1 | 2 |'].join('\n')
    render(
      <TestWrapper>
        <MarkdownContent content={table} />
      </TestWrapper>,
    )
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'A' })).toBeInTheDocument()
  })

  it('opens links in a new tab with a safe rel attribute', () => {
    render(
      <TestWrapper>
        <MarkdownContent content="[docs](https://example.com)" />
      </TestWrapper>,
    )
    const link = screen.getByRole('link', { name: 'docs' })
    expect(link).toHaveAttribute('href', 'https://example.com')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
  })

  it('escapes embedded raw HTML instead of executing it (XSS guard)', () => {
    render(
      <TestWrapper>
        <MarkdownContent content={`${RAW_IMAGE_HTML}hi`} />
      </TestWrapper>,
    )
    // The image markup must not become a real element; react-markdown escapes it.
    expect(document.querySelector('img')).toBeNull()
    // The raw markup is shown as literal text instead.
    expect(screen.getByText((content: string) => content.includes(`${RAW_IMAGE_HTML}hi`))).toBeInTheDocument()
  })

  it('strips dangerous javascript: link URIs', () => {
    render(
      <TestWrapper>
        <MarkdownContent content="[click](javascript:alert(1))" />
      </TestWrapper>,
    )
    // react-markdown sanitizes the URI to an empty href, so the anchor no
    // longer exposes the "link" role — query by its text instead.
    const link = screen.getByText('click').closest('a')
    expect(link).not.toBeNull()
    expect(link?.getAttribute('href') ?? '').not.toContain('javascript:')
  })

  it('renders inline images as a click-through link, not an auto-loading element', () => {
    render(
      <TestWrapper>
        <MarkdownContent content="![a cat](https://example.com/cat.png)" />
      </TestWrapper>,
    )
    // No image element is emitted, so the untrusted URL is not fetched on render.
    expect(document.querySelector('img')).toBeNull()
    // Instead the operator gets a safe link they can choose to open.
    const link = screen.getByRole('link', { name: 'a cat' })
    expect(link).toHaveAttribute('href', 'https://example.com/cat.png')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
  })
})
