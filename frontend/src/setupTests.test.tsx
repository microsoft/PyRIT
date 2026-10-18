import type { CSSProperties } from 'react'

import { Dialog, DialogSurface, FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

describe('JSDOM focus layout', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('should provide layout for displayed controls', () => {
    render(<button type="button">Visible control</button>)

    expect(screen.getByRole('button', { name: 'Visible control' }).offsetParent).not.toBeNull()
    expect(document.body.getBoundingClientRect().width).toBe(window.innerWidth)
    expect(document.body.getBoundingClientRect().height).toBe(window.innerHeight)
    expect(document.body.offsetParent).toBeNull()
    expect(document.documentElement.offsetParent).toBeNull()
  })

  it('should not provide layout for detached controls', () => {
    const button = document.createElement('button')

    expect(button.offsetParent).toBeNull()
  })

  it('should not provide layout for hidden controls or their descendants', () => {
    const { rerender } = render(
      <>
        <button type="button" hidden>Hidden control</button>
        <div hidden><button type="button">Hidden descendant</button></div>
      </>,
    )

    expect(screen.getByText('Hidden control').offsetParent).toBeNull()
    expect(screen.getByText('Hidden descendant').offsetParent).toBeNull()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()

    rerender(
      <>
        <button type="button">Hidden control</button>
        <div><button type="button">Hidden descendant</button></div>
      </>,
    )
    expect(screen.getByRole('button', { name: 'Hidden control' }).offsetParent).not.toBeNull()
    expect(screen.getByRole('button', { name: 'Hidden descendant' }).offsetParent).not.toBeNull()
  })

  it.each<CSSProperties>([
    { display: 'none' },
    { visibility: 'hidden' },
  ])('should preserve CSS-hidden controls (%j)', (style: CSSProperties) => {
    const { rerender } = render(<div style={style}><button type="button">CSS-hidden control</button></div>)

    expect(screen.queryByRole('button', { name: 'CSS-hidden control' })).not.toBeInTheDocument()
    if (style.display === 'none') {
      expect(screen.getByText('CSS-hidden control').offsetParent).toBeNull()
    }

    rerender(<div><button type="button">CSS-hidden control</button></div>)
    expect(screen.getByRole('button', { name: 'CSS-hidden control' }).offsetParent).not.toBeNull()
  })

  it('should keep a hidden body zero-sized', () => {
    render(<button type="button">Body descendant</button>)
    const button = screen.getByRole('button', { name: 'Body descendant' })
    const originalDisplay = document.body.style.display
    try {
      document.body.style.display = 'none'

      expect(document.body.getBoundingClientRect().width).toBe(0)
      expect(document.body.getBoundingClientRect().height).toBe(0)
      expect(button.offsetParent).toBeNull()
    } finally {
      document.body.style.display = originalDisplay
    }
  })

  it('should preserve fixed positioning and disabled controls', async () => {
    const user = userEvent.setup()
    render(
      <>
        <button type="button" style={{ position: 'fixed' }}>Fixed control</button>
        <button type="button" disabled>Disabled control</button>
      </>,
    )

    const fixedButton = screen.getByRole('button', { name: 'Fixed control' })
    const disabledButton = screen.getByRole('button', { name: 'Disabled control' })
    expect(fixedButton.offsetParent).toBeNull()
    expect(disabledButton).toBeDisabled()
    await user.click(disabledButton)
    expect(disabledButton).not.toHaveFocus()
  })

  it('should focus only visible, enabled dialog controls', () => {
    render(
      <FluentProvider theme={webLightTheme}>
        <Dialog open>
          <DialogSurface aria-label="Focus test">
            <button type="button" hidden>Hidden control</button>
            <div style={{ display: 'none' }}><button type="button">Hidden descendant</button></div>
            <div style={{ visibility: 'hidden' }}><button type="button">Invisible descendant</button></div>
            <button type="button" aria-hidden="true">Accessibility-hidden control</button>
            <button type="button" disabled>Disabled control</button>
            <fieldset disabled><button type="button">Disabled descendant</button></fieldset>
            <button type="button">Available control</button>
          </DialogSurface>
        </Dialog>
      </FluentProvider>,
    )

    expect(screen.getByRole('button', { name: 'Available control' })).toHaveFocus()
  })
})
