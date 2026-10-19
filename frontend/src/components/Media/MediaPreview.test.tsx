import { useState, type ComponentProps } from 'react'

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import MediaPreview from './MediaPreview'

function renderPreview(props: Partial<ComponentProps<typeof MediaPreview>> & Pick<ComponentProps<typeof MediaPreview>, 'type' | 'src'>) {
  return render(
    <FluentProvider theme={webLightTheme}>
      <MediaPreview {...props} />
    </FluentProvider>,
  )
}

describe('MediaPreview', () => {
  it('should render chat image, audio, and video immediately and never autoplay', () => {
    renderPreview({ type: 'image', src: 'data:image/png;base64,AAAA', alt: 'chart' })
    const image = screen.getByAltText('chart')
    expect(image).toBeInTheDocument()
    expect(image).not.toHaveAttribute('loading')
    expect(screen.queryByRole('button', { name: /preview/i })).not.toBeInTheDocument()

    renderPreview({ type: 'audio', src: 'https://example.test/talk.wav' })
    const audio = screen.getByTestId('audio-player')
    expect(audio).toHaveAttribute('src', 'https://example.test/talk.wav')
    expect(audio).toHaveAttribute('controls')
    expect(audio).not.toHaveAttribute('autoplay')
    expect(audio).not.toHaveAttribute('preload')

    renderPreview({ type: 'video', src: 'https://example.test/clip.mp4' })
    const video = screen.getByTestId('video-player')
    expect(video).not.toHaveAttribute('autoplay')
    expect((video as HTMLVideoElement).paused).toBe(true)
  })

  it('should keep the chat failure text', () => {
    renderPreview({ type: 'video', src: 'https://example.test/broken.mp4' })
    fireEvent.error(screen.getByTestId('video-player'))
    expect(screen.getByTestId('video-error')).toHaveTextContent('Video failed to load')
    expect(screen.queryByRole('button', { name: 'Retry video' })).not.toBeInTheDocument()

    renderPreview({ type: 'audio', src: 'https://example.test/broken.wav' })
    fireEvent.error(screen.getByTestId('audio-player'))
    expect(screen.getByTestId('audio-error')).toHaveTextContent('Audio failed to load')

    renderPreview({ type: 'image', src: 'https://example.test/broken.png', alt: 'broken' })
    fireEvent.error(screen.getByAltText('broken'))
    expect(screen.getByTestId('image-error')).toHaveTextContent('Image failed to load')
  })

  it('should load lazily, stay paused, and stop when the preview is dismissed', async () => {
    const user = userEvent.setup()
    renderPreview({ type: 'video', src: 'https://example.test/clip.mp4', lazy: true })
    expect(screen.queryByTestId('video-player')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Show video preview' }))
    const video = screen.getByTestId('video-player') as HTMLVideoElement
    Object.defineProperty(video, 'paused', { configurable: true, get: () => false })
    const pause = jest.spyOn(video, 'pause').mockImplementation(() => {})
    expect(video).toHaveAttribute('preload', 'none')
    expect(video.autoplay).toBe(false)

    await user.click(screen.getByRole('button', { name: 'Hide video preview' }))
    expect(pause).toHaveBeenCalled()
    expect(screen.queryByTestId('video-player')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Show video preview' })).toBeInTheDocument()
  })

  it('should pause playback when the preview leaves the page', () => {
    const { unmount } = renderPreview({ type: 'audio', src: 'https://example.test/talk.wav' })
    const audio = screen.getByTestId('audio-player') as HTMLAudioElement
    Object.defineProperty(audio, 'paused', { configurable: true, get: () => false })
    const pause = jest.spyOn(audio, 'pause').mockImplementation(() => {})
    unmount()
    expect(pause).toHaveBeenCalled()
  })

  it('should retry and offer download after a failed on-demand load', async () => {
    const user = userEvent.setup()
    renderPreview({
      type: 'image',
      src: '/api/media?path=%2Fdbdata%2Fpic.png',
      alt: 'pic',
      lazy: true,
      recovery: true,
    })
    expect(screen.queryByAltText('pic')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Show image preview' }))
    fireEvent.error(screen.getByAltText('pic'))
    expect(screen.getByTestId('image-error')).toHaveTextContent(
      'Image failed to load. The file may be missing, or the link may be expired or inaccessible.',
    )
    const download = screen.getByRole('link', { name: 'Download' })
    expect(download).toHaveAttribute('href', '/api/media?path=%2Fdbdata%2Fpic.png')
    expect(download).toHaveAttribute('download', 'pic.png')
    expect(download).not.toHaveAttribute('target')

    await user.click(screen.getByRole('button', { name: 'Retry image' }))
    expect(screen.getByAltText('pic')).toBeInTheDocument()
    expect(screen.queryByTestId('image-error')).not.toBeInTheDocument()
  })

  it('should open a cross-origin file in a new tab instead of leaving the page', async () => {
    const user = userEvent.setup()
    const signed = 'https://account.blob.core.windows.net/container/clip.mp4?sig=expired'
    renderPreview({ type: 'video', src: signed, lazy: true, recovery: true })
    await user.click(screen.getByRole('button', { name: 'Show video preview' }))
    fireEvent.error(screen.getByTestId('video-player'))

    const link = screen.getByRole('link', { name: 'Open in new tab' })
    expect(link).toHaveAttribute('href', signed)
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    expect(link).not.toHaveAttribute('download')
  })

  it('should retry with a newly signed url and refresh again when the preview is reopened', async () => {
    const user = userEvent.setup()
    const expired = 'https://account.blob.core.windows.net/container/pic.png?sig=expired'
    const fresh = 'https://account.blob.core.windows.net/container/pic.png?sig=fresh'
    const refresh = jest.fn(async () => fresh)

    function Harness() {
      const [src, setSrc] = useState(expired)
      return (
        <FluentProvider theme={webLightTheme}>
          <MediaPreview
            type="image"
            src={src}
            alt="pic"
            lazy
            recovery
            onRetry={async () => {
              const next = await refresh()
              await Promise.resolve()
              setSrc(next)
              return next
            }}
          />
        </FluentProvider>
      )
    }

    render(<Harness />)
    await user.click(screen.getByRole('button', { name: 'Show image preview' }))
    expect(screen.getByAltText('pic')).toHaveAttribute('src', expired)
    expect(refresh).not.toHaveBeenCalled()

    fireEvent.error(screen.getByAltText('pic'))
    await user.click(screen.getByRole('button', { name: 'Retry image' }))
    await waitFor(() => expect(screen.getByAltText('pic')).toHaveAttribute('src', fresh))
    expect(screen.queryByTestId('image-error')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Hide image preview' }))
    await user.click(screen.getByRole('button', { name: 'Show image preview' }))
    await waitFor(() => expect(refresh).toHaveBeenCalledTimes(2))
    expect(screen.getByAltText('pic')).toHaveAttribute('src', fresh)
  })

  it('should keep the error visible when refreshing the link fails, then allow another retry', async () => {
    const user = userEvent.setup()
    const expired = 'https://account.blob.core.windows.net/container/pic.png?sig=expired'
    const fresh = 'https://account.blob.core.windows.net/container/pic.png?sig=fresh'
    let attempts = 0
    renderPreview({
      type: 'image',
      src: expired,
      alt: 'pic',
      lazy: true,
      recovery: true,
      onRetry: async () => {
        attempts += 1
        if (attempts === 1) throw new Error('unavailable')
        return fresh
      },
    })
    await user.click(screen.getByRole('button', { name: 'Show image preview' }))
    fireEvent.error(screen.getByAltText('pic'))
    await user.click(screen.getByRole('button', { name: 'Retry image' }))
    await waitFor(() => expect(screen.getByTestId('image-refresh-error')).toHaveTextContent("Couldn't refresh the link."))
    expect(screen.getByTestId('image-error')).toBeInTheDocument()
    expect(screen.queryByAltText('pic')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry image' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Retry image' }))
    await waitFor(() => expect(screen.getByAltText('pic')).toHaveAttribute('src', fresh))
    expect(screen.queryByTestId('image-refresh-error')).not.toBeInTheDocument()
    expect(screen.queryByTestId('image-error')).not.toBeInTheDocument()
  })

  it('should wait for one refresh when Show is clicked twice after hiding the preview', async () => {
    const user = userEvent.setup()
    const expired = 'https://account.blob.core.windows.net/container/pic.png?sig=expired'
    const fresh = 'https://account.blob.core.windows.net/container/pic.png?sig=fresh'
    let finishRefresh: (value: string) => void = () => {}
    const refreshedUrl = new Promise<string>((resolve) => { finishRefresh = resolve })
    const refresh = jest.fn(() => refreshedUrl)
    renderPreview({ type: 'image', src: expired, alt: 'pic', lazy: true, recovery: true, onRetry: refresh })

    await user.click(screen.getByRole('button', { name: 'Show image preview' }))
    fireEvent.load(screen.getByAltText('pic'))
    await user.click(screen.getByRole('button', { name: 'Hide image preview' }))
    const show = screen.getByRole('button', { name: 'Show image preview' })
    await user.dblClick(show)

    expect(show).toBeDisabled()
    expect(show).toHaveAttribute('aria-busy', 'true')
    expect(refresh).toHaveBeenCalledTimes(1)
    expect(screen.queryByAltText('pic')).not.toBeInTheDocument()
    await act(async () => { finishRefresh(fresh) })
    expect(screen.getByAltText('pic')).toHaveAttribute('src', fresh)
    expect(screen.queryByTestId('image-error')).not.toBeInTheDocument()
  })

  it.each(['cancelled', 'failed'] as const)(
    'should keep a reopened preview closed after a %s refresh and allow another attempt',
    async (failure: 'cancelled' | 'failed') => {
      const user = userEvent.setup()
      const expired = 'https://account.blob.core.windows.net/container/pic.png?sig=expired'
      const fresh = 'https://account.blob.core.windows.net/container/pic.png?sig=fresh'
      const reason = failure === 'cancelled'
        ? new DOMException('The example changed.', 'AbortError')
        : new Error('unavailable')
      const refresh = jest.fn(async (): Promise<string> => fresh).mockRejectedValueOnce(reason)
      renderPreview({ type: 'image', src: expired, alt: 'pic', lazy: true, recovery: true, onRetry: refresh })

      await user.click(screen.getByRole('button', { name: 'Show image preview' }))
      fireEvent.load(screen.getByAltText('pic'))
      await user.click(screen.getByRole('button', { name: 'Hide image preview' }))
      await user.click(screen.getByRole('button', { name: 'Show image preview' }))

      expect(screen.queryByAltText('pic')).not.toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Show image preview' })).toBeEnabled()
      if (failure === 'failed') {
        expect(screen.getByRole('alert')).toHaveTextContent("Couldn't refresh the link.")
      } else {
        expect(screen.queryByRole('alert')).not.toBeInTheDocument()
      }

      await user.click(screen.getByRole('button', { name: 'Show image preview' }))
      expect(screen.getByAltText('pic')).toHaveAttribute('src', fresh)
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    },
  )

  it('should not show a refresh failure that lands after the preview is closed', async () => {
    const user = userEvent.setup()
    let rejectRefresh: (err: Error) => void = () => {}
    let attempts = 0
    renderPreview({
      type: 'image',
      src: 'https://account.blob.core.windows.net/container/pic.png?sig=expired',
      alt: 'pic',
      lazy: true,
      recovery: true,
      onRetry: () => {
        attempts += 1
        if (attempts === 1) return new Promise((_, reject) => { rejectRefresh = reject })
        return undefined
      },
    })
    await user.click(screen.getByRole('button', { name: 'Show image preview' }))
    fireEvent.error(screen.getByAltText('pic'))
    await user.click(screen.getByRole('button', { name: 'Retry image' }))
    await user.click(screen.getByRole('button', { name: 'Hide image preview' }))
    await act(async () => {
      rejectRefresh(new Error('late'))
    })
    await user.click(screen.getByRole('button', { name: 'Show image preview' }))
    fireEvent.error(screen.getByAltText('pic'))
    expect(screen.getByTestId('image-error')).toBeInTheDocument()
    expect(screen.queryByTestId('image-refresh-error')).not.toBeInTheDocument()
  })

  it('should not retry the current url when the refresh is aborted', async () => {
    const user = userEvent.setup()
    renderPreview({
      type: 'image',
      src: 'https://account.blob.core.windows.net/container/pic.png?sig=expired',
      alt: 'pic',
      lazy: true,
      recovery: true,
      onRetry: () => Promise.reject(new DOMException('The example changed before the link refreshed.', 'AbortError')),
    })
    await user.click(screen.getByRole('button', { name: 'Show image preview' }))
    fireEvent.error(screen.getByAltText('pic'))
    await user.click(screen.getByRole('button', { name: 'Retry image' }))
    expect(screen.getByTestId('image-error')).toBeInTheDocument()
    expect(screen.queryByTestId('image-refresh-error')).not.toBeInTheDocument()
    expect(screen.queryByAltText('pic')).not.toBeInTheDocument()
  })
})
