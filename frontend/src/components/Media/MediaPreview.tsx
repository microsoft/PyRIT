import { useCallback, useEffect, useRef, useState } from 'react'

import { Button, Spinner, Text } from '@fluentui/react-components'

import { basenameFromValue, mediaAccessLink } from '@/utils/media'

import { useMediaPreviewStyles } from './MediaPreview.styles'

export interface MediaPreviewProps {
  readonly type: 'image' | 'audio' | 'video'
  readonly src: string
  readonly alt?: string
  readonly className?: string
  readonly hiddenClassName?: string
  readonly containerClassName?: string
  readonly spinnerClassName?: string
  /** Mount the media element only after the user asks. Chat leaves this off. */
  readonly lazy?: boolean
  /** Offer retry and download after a failed load. Chat leaves this off. */
  readonly recovery?: boolean
  /**
   * Dataset details refetch a signed display URL before trying again.
   * Return that URL so this preview can use it in the same update.
   * Chat leaves this unset, so a retry only remounts the current src.
   */
  readonly onRetry?: () => Promise<string | void> | string | void
}

function isAbortError(err: unknown): boolean {
  return typeof err === 'object' && err != null && 'name' in err && (err as { name?: string }).name === 'AbortError'
}

function stopPlayback(media: HTMLMediaElement | null): void {
  if (!media || media.paused) return
  media.pause()
}

function failureMessage(type: MediaPreviewProps['type'], recovery: boolean): string {
  const label = type === 'image' ? 'Image' : type === 'audio' ? 'Audio' : 'Video'
  if (!recovery) return `${label} failed to load`
  return `${label} failed to load. The file may be missing, or the link may be expired or inaccessible.`
}

export function MediaAccessLink({
  url,
  filename,
  className,
}: {
  url: string
  filename: string
  className?: string
}) {
  const link = mediaAccessLink(url, filename, window.location.origin)
  return (
    <a
      className={className}
      href={link.href}
      rel="noopener noreferrer"
      {...(link.downloadName != null ? { download: link.downloadName } : {})}
      {...(link.openInNewTab ? { target: '_blank' } : {})}
    >
      {link.label}
    </a>
  )
}

function RecoveryActions({
  type,
  src,
  onRetry,
  refreshFailed,
}: {
  type: MediaPreviewProps['type']
  src: string
  onRetry: () => void
  refreshFailed: boolean
}) {
  const styles = useMediaPreviewStyles()
  const label = type === 'image' ? 'image' : type === 'audio' ? 'audio' : 'video'
  return (
    <div className={styles.recovery}>
      <Text size={200} italic data-testid={`${type}-error`}>{failureMessage(type, true)}</Text>
      {refreshFailed
        ? <Text size={200} data-testid={`${type}-refresh-error`}>{"Couldn't refresh the link."}</Text>
        : null}
      <div className={styles.actions}>
        <Button className={styles.button} appearance="secondary" onClick={() => { void onRetry() }}>
          {`Retry ${label}`}
        </Button>
        <MediaAccessLink className={styles.download} url={src} filename={basenameFromValue(src, label)} />
      </div>
    </div>
  )
}

/**
 * Shared image, audio, and video preview used by chat and dataset details.
 * Playback is never automatic. Lazy callers mount the element only on request
 * and pause it when the preview is hidden or leaves the page.
 */
export default function MediaPreview({
  type,
  src,
  alt = '',
  className,
  hiddenClassName,
  containerClassName,
  spinnerClassName,
  lazy = false,
  recovery = false,
  onRetry,
}: MediaPreviewProps) {
  const styles = useMediaPreviewStyles()
  const mediaRef = useRef<HTMLMediaElement | null>(null)
  const openedOnce = useRef(false)
  const refreshSerial = useRef(0)
  const setMediaRef = useCallback((node: HTMLMediaElement | null) => {
    mediaRef.current = node
  }, [])
  const [open, setOpen] = useState(!lazy)
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState(false)
  const [refreshFailed, setRefreshFailed] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [attempt, setAttempt] = useState(0)
  // Hold a refreshed URL until the src prop catches up, without an effect.
  const [replacement, setReplacement] = useState<{ readonly from: string; readonly next: string } | null>(null)
  const activeSrc = replacement != null && replacement.from === src ? replacement.next : src
  const onLoad = useCallback(() => setLoaded(true), [])
  const onError = useCallback(() => {
    setError(true)
    setLoaded(true)
  }, [])

  useEffect(() => {
    const media = mediaRef.current
    return () => {
      stopPlayback(media)
    }
  }, [open, attempt, error])

  useEffect(() => {
    return () => {
      refreshSerial.current += 1
    }
  }, [])

  const refreshDisplayUrl = async (): Promise<boolean> => {
    const serial = ++refreshSerial.current
    setRefreshing(true)
    setRefreshFailed(false)
    try {
      const next = await onRetry?.()
      if (serial !== refreshSerial.current) return false
      if (typeof next === 'string' && next.length > 0) setReplacement({ from: src, next })
      setRefreshFailed(false)
      return true
    } catch (err: unknown) {
      if (serial !== refreshSerial.current || isAbortError(err)) return false
      setRefreshFailed(true)
      return false
    } finally {
      if (serial === refreshSerial.current) setRefreshing(false)
    }
  }

  const retry = async (): Promise<void> => {
    const refreshed = await refreshDisplayUrl()
    if (!refreshed) return
    setError(false)
    setLoaded(false)
    setAttempt((current) => current + 1)
  }

  const reveal = async (): Promise<void> => {
    // The first open uses the URL from the detail that was just loaded.
    // Showing it again, after Hide, asks for a new signature first.
    if (openedOnce.current && !(await refreshDisplayUrl())) return
    openedOnce.current = true
    setOpen(true)
  }

  const hide = (): void => {
    refreshSerial.current += 1
    stopPlayback(mediaRef.current)
    setOpen(false)
    setError(false)
    setRefreshFailed(false)
    setRefreshing(false)
    setLoaded(false)
  }

  if (!open) {
    return (
      <>
        <Button
          className={styles.button}
          appearance="secondary"
          disabled={refreshing}
          aria-busy={refreshing}
          onClick={() => { void reveal() }}
        >
          {`Show ${type} preview`}
        </Button>
        {refreshFailed && (
          <Text size={200} role="alert" data-testid={`${type}-refresh-error`}>
            {"Couldn't refresh the link. Try showing the preview again."}
          </Text>
        )}
      </>
    )
  }

  const hideButton = lazy ? (
    <Button className={styles.button} appearance="secondary" onClick={hide}>
      {`Hide ${type} preview`}
    </Button>
  ) : null

  if (type === 'image') {
    return (
      <div className={containerClassName ?? styles.imageContainer}>
        {hideButton}
        {!loaded && !error && <Spinner size="small" className={spinnerClassName ?? styles.spinner} />}
        {error
          ? (recovery
            ? <RecoveryActions type={type} src={activeSrc} onRetry={retry} refreshFailed={refreshFailed} />
            : <Text size={200} italic data-testid="image-error">{failureMessage(type, false)}</Text>)
          : <img
              key={attempt}
              src={activeSrc}
              alt={alt}
              className={loaded ? (className ?? styles.image) : (hiddenClassName ?? styles.imageHidden)}
              loading={lazy ? 'lazy' : undefined}
              onLoad={onLoad}
              onError={onError}
            />
        }
      </div>
    )
  }

  if (error) {
    return (
      <>
        {hideButton}
        {recovery
          ? <RecoveryActions type={type} src={activeSrc} onRetry={retry} refreshFailed={refreshFailed} />
          : <Text size={200} italic data-testid={`${type}-error`}>{failureMessage(type, false)}</Text>}
      </>
    )
  }

  const mediaClassName = className ?? (type === 'video' ? styles.video : styles.audio)
  const preload = lazy ? 'none' : undefined

  return (
    <>
      {hideButton}
      {type === 'video' ? (
        <video
          key={attempt}
          ref={setMediaRef}
          src={activeSrc}
          controls
          autoPlay={false}
          preload={preload}
          className={mediaClassName}
          onError={onError}
          data-testid="video-player"
        />
      ) : (
        <audio
          key={attempt}
          ref={setMediaRef}
          src={activeSrc}
          controls
          autoPlay={false}
          preload={preload}
          className={mediaClassName}
          onError={onError}
          data-testid="audio-player"
        />
      )}
    </>
  )
}
