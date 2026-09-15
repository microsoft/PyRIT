import { afterEach, beforeEach, jest } from '@jest/globals'

function hasLayoutBox(element: HTMLElement): boolean {
  if (!element.isConnected) {
    return false
  }

  for (let ancestor: HTMLElement | null = element; ancestor; ancestor = ancestor.parentElement) {
    if (getComputedStyle(ancestor).display === 'none') {
      return false
    }
  }
  return true
}

/**
 * Supply the layout signals Fluent UI uses to find focusable controls.
 * JSDOM otherwise reports a zero-sized body and null offset parents for every element.
 */
export function mockJsdomLayout(): void {
  let offsetParent: jest.SpiedGetter<Element | null>
  let bodyRect: jest.SpiedFunction<HTMLElement['getBoundingClientRect']>

  beforeEach(() => {
    offsetParent = jest.spyOn(HTMLElement.prototype, 'offsetParent', 'get')
      .mockImplementation(getOffsetParent)
    bodyRect = jest.spyOn(document.body, 'getBoundingClientRect').mockImplementation(() => hasLayoutBox(document.body)
      ? new DOMRect(0, 0, window.innerWidth, window.innerHeight)
      : new DOMRect())
  })

  afterEach(() => {
    bodyRect.mockRestore()
    offsetParent.mockRestore()
  })
}

function getOffsetParent(this: HTMLElement): HTMLElement | null {
  if (
    this === this.ownerDocument.body
    || this === this.ownerDocument.documentElement
    || !hasLayoutBox(this)
    || getComputedStyle(this).position === 'fixed'
  ) {
    return null
  }
  return this.parentElement
}
