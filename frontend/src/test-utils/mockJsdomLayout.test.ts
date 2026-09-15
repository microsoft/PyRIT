import { mockJsdomLayout } from './mockJsdomLayout'

mockJsdomLayout()

describe('JSDOM focus layout', () => {
  let container: HTMLDivElement
  let button: HTMLButtonElement

  beforeEach(() => {
    container = document.createElement('div')
    button = document.createElement('button')
    container.append(button)
    document.body.append(container)
  })

  afterEach(() => {
    container.remove()
  })

  it('should provide a layout parent for displayed controls and a nonempty viewport', () => {
    expect(button.offsetParent).toBe(container)
    expect(document.body.getBoundingClientRect().width).toBe(window.innerWidth)
    expect(document.body.getBoundingClientRect().height).toBe(window.innerHeight)
    expect(document.body.offsetParent).toBeNull()
    expect(document.documentElement.offsetParent).toBeNull()
  })

  it('should not give detached controls a layout parent', () => {
    container.remove()

    expect(button.offsetParent).toBeNull()
  })

  it.each(['control', 'ancestor'])('should preserve display: none on a %s', (target: string) => {
    const hiddenElement = target === 'control' ? button : container
    hiddenElement.style.display = 'none'

    expect(button.offsetParent).toBeNull()

    hiddenElement.style.removeProperty('display')
    expect(button.offsetParent).toBe(container)
  })

  it.each(['control', 'ancestor'])('should preserve the hidden attribute on a %s', (target: string) => {
    const hiddenElement = target === 'control' ? button : container
    hiddenElement.hidden = true

    expect(button.offsetParent).toBeNull()

    hiddenElement.hidden = false
    expect(button.offsetParent).toBe(container)
  })

  it('should keep fixed-position controls without an offset parent', () => {
    button.style.position = 'fixed'

    expect(button.offsetParent).toBeNull()
  })

  it('should keep a hidden body zero-sized', () => {
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
})
