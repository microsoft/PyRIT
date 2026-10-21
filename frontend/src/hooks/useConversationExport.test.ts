import { act, renderHook } from '@testing-library/react'

import type { Message } from '@/types'
import { exportConversation } from '@/utils/conversationExport'

import { useConversationExport } from './useConversationExport'
import type { UseConversationExportOptions } from './useConversationExport'

jest.mock('@/utils/conversationExport', () => ({ exportConversation: jest.fn() }))

const userMessage: Message = { role: 'user', content: 'Prompt', timestamp: '' }

function renderExport(overrides: Partial<UseConversationExportOptions> = {}) {
  return renderHook(
    (props: UseConversationExportOptions) => useConversationExport(props),
    { initialProps: { messages: [userMessage], conversationId: 'main', blocked: false, ...overrides } },
  )
}

describe('useConversationExport', () => {
  beforeEach(() => jest.resetAllMocks())
  afterEach(() => jest.restoreAllMocks())

  describe('canExport', () => {
    it.each([
      ['an empty transcript', [], false],
      ['only a loading placeholder', [{ role: 'assistant', content: '...', timestamp: '', isLoading: true }], false],
      ['only a system prompt', [{ role: 'system', content: 'System', timestamp: '' }], false],
      ['a user message', [userMessage], true],
    ])('should report %s as exportable: %s', (_case: string, messages: Message[], expected: boolean) => {
      const { result } = renderExport({ messages })
      expect(result.current.canExport).toBe(expected)
    })

    it('should be false while the transcript is blocked', () => {
      const { result } = renderExport({ blocked: true })
      expect(result.current.canExport).toBe(false)
    })
  })

  it('should export the displayed messages and conversation in the chosen format', async () => {
    jest.mocked(exportConversation).mockResolvedValue(undefined)
    const { result } = renderExport({ conversationId: 'branch' })

    await act(async () => { await result.current.exportAs('html') })

    expect(exportConversation).toHaveBeenCalledWith({ messages: [userMessage], conversationId: 'branch', format: 'html' })
    expect(result.current.isExporting).toBe(false)
  })

  it('should ignore a second export requested in the same tick and allow one after it finishes', async () => {
    let finish: () => void = () => {}
    jest.mocked(exportConversation).mockImplementationOnce(() => new Promise<void>((resolve) => { finish = resolve }))
    const { result } = renderExport()

    let first: Promise<void> = Promise.resolve()
    act(() => {
      first = result.current.exportAs('markdown')
      void result.current.exportAs('json')
    })
    expect(exportConversation).toHaveBeenCalledTimes(1)
    expect(result.current.isExporting).toBe(true)

    await act(async () => {
      finish()
      await first
    })
    expect(result.current.isExporting).toBe(false)

    jest.mocked(exportConversation).mockResolvedValueOnce(undefined)
    await act(async () => { await result.current.exportAs('json') })
    expect(exportConversation).toHaveBeenCalledTimes(2)
    expect(exportConversation).toHaveBeenLastCalledWith(expect.objectContaining({ format: 'json' }))
  })

  it('should log a failed export and release the lock', async () => {
    const error = new Error('Download failed')
    const consoleError = jest.spyOn(console, 'error').mockImplementation(() => {})
    jest.mocked(exportConversation).mockRejectedValueOnce(error).mockResolvedValueOnce(undefined)
    const { result } = renderExport()

    await act(async () => { await result.current.exportAs('markdown') })
    expect(consoleError).toHaveBeenCalledWith('Failed to export conversation:', error)
    expect(result.current.isExporting).toBe(false)

    await act(async () => { await result.current.exportAs('markdown') })
    expect(exportConversation).toHaveBeenCalledTimes(2)
  })
})
