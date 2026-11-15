import { act, renderHook, waitFor } from '@testing-library/react'

import { mediaApi } from '@/services/api'
import type { MessageAttachment } from '@/types'
import { startAttachmentUpload, uploadedFileValue } from '@/utils/messageMapper'

import { useAttachmentUploads } from './useAttachmentUploads'

jest.mock('@/services/api', () => ({
  mediaApi: { upload: jest.fn() },
}))

function attachment(file: File): MessageAttachment {
  return { draftId: 'draft-file', file, type: 'image', name: file.name, url: 'blob:draft', mimeType: file.type }
}

describe('useAttachmentUploads', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    jest.mocked(mediaApi.upload).mockResolvedValue({ value: 'pyrit-upload:retained' })
  })

  it('shares failed lease refreshes and pending retries across mounted consumers', async () => {
    const file = new File(['image'], 'retained.png', { type: 'image/png' })
    const attachments = [attachment(file)]
    const composer = renderHook(() => useAttachmentUploads(attachments))
    const editor = renderHook(() => useAttachmentUploads(attachments))
    await waitFor(() => expect(composer.result.current.pending).toBe(false))
    expect(editor.result.current.pending).toBe(false)
    expect(mediaApi.upload).toHaveBeenCalledTimes(1)

    const now = jest.spyOn(Date, 'now').mockReturnValue(Date.now() + 24 * 60 * 60 * 1000)
    try {
      jest.mocked(mediaApi.upload).mockRejectedValueOnce(new Error('Refresh unavailable'))
      await act(async () => {
        await expect(uploadedFileValue(file)).rejects.toThrow('Refresh unavailable')
      })
      expect(composer.result.current.errors).toEqual([
        { id: 'draft-file', file, detail: 'Refresh unavailable' },
      ])
      expect(editor.result.current.errors).toEqual(composer.result.current.errors)

      let finish: (value: { value: string }) => void = () => {}
      jest.mocked(mediaApi.upload).mockReturnValueOnce(new Promise((resolve) => { finish = resolve }))
      act(() => { editor.result.current.retry(file) })
      expect(composer.result.current.pending).toBe(true)
      expect(editor.result.current.pending).toBe(true)
      expect(composer.result.current.errors).toEqual([])
      await waitFor(() => expect(mediaApi.upload).toHaveBeenCalledTimes(3))
      await act(async () => { finish({ value: 'pyrit-upload:renewed' }) })
      expect(composer.result.current.pending).toBe(false)
      expect(editor.result.current.errors).toEqual([])
      await expect(uploadedFileValue(file)).resolves.toBe('pyrit-upload:renewed')
      expect(mediaApi.upload).toHaveBeenCalledTimes(3)
    } finally {
      now.mockRestore()
    }
  })

  it('does not replace a successful retry with an older failed upload', async () => {
    const file = new File(['image'], 'retry.png', { type: 'image/png' })
    const attachments = [attachment(file)]
    let failInitial: (error: Error) => void = () => {}
    jest.mocked(mediaApi.upload).mockReturnValueOnce(new Promise((_resolve, reject) => { failInitial = reject }))
    const { result } = renderHook(() => useAttachmentUploads(attachments))
    await waitFor(() => expect(mediaApi.upload).toHaveBeenCalledTimes(1))
    act(() => { result.current.retry(file) })
    await waitFor(() => expect(result.current.pending).toBe(false))
    await act(async () => { failInitial(new Error('Obsolete failure')) })
    expect(result.current.errors).toEqual([])
    await expect(uploadedFileValue(file)).resolves.toBe('pyrit-upload:retained')
  })

  it('unsubscribes removed files and observes the current cache on reattachment', async () => {
    const file = new File(['image'], 'removed.png', { type: 'image/png' })
    const attachments = [attachment(file)]
    const { result, rerender } = renderHook(
      ({ files }: { files: MessageAttachment[] }) => useAttachmentUploads(files),
      { initialProps: { files: attachments } },
    )
    await waitFor(() => expect(result.current.pending).toBe(false))
    rerender({ files: [] })
    jest.mocked(mediaApi.upload).mockRejectedValueOnce(new Error('Detached refresh'))
    await act(async () => { await startAttachmentUpload(file, true) })
    expect(result.current.errors).toEqual([])
    rerender({ files: attachments })
    await waitFor(() => expect(result.current.errors[0]?.detail).toBe('Detached refresh'))
  })
})
