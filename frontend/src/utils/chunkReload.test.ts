import { isChunkLoadError } from './chunkReload'

describe('isChunkLoadError', () => {
  it.each([
    'Failed to fetch dynamically imported module: http://x/assets/A.js',
    'Importing a module script failed.',
    'error loading dynamically imported module: http://x/assets/A.js',
    'Unable to preload CSS for /assets/A.css',
  ])('recognizes %s', (message) => {
    expect(isChunkLoadError(new TypeError(message))).toBe(true)
  })

  it('ignores other errors', () => {
    expect(isChunkLoadError(new Error('Cannot read properties of undefined'))).toBe(false)
    expect(isChunkLoadError('Failed to fetch dynamically imported module')).toBe(false)
  })
})
