import { useState } from 'react'
import { ErrorBoundary as ReactErrorBoundary } from 'react-error-boundary'
import type { FallbackProps } from 'react-error-boundary'
import type { ReactNode } from 'react'
import {
  Button,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
  MessageBar,
  MessageBarBody,
  tokens,
} from '@fluentui/react-components'
import { isChunkLoadError } from '@/utils/chunkReload'

/**
 * Where the fallback appears: 'page' fills the window, 'section' takes the place of the failed
 * part (for example one screen inside the app shell), and 'dialog' replaces a failed dialog.
 */
export type ErrorBoundaryVariant = 'page' | 'section' | 'dialog'

interface ErrorFallbackProps extends FallbackProps {
  variant: ErrorBoundaryVariant
  onDismiss?: () => void
}

function ErrorFallback({ error, resetErrorBoundary, variant, onDismiss }: ErrorFallbackProps) {
  const [crashCount, setCrashCount] = useState(1)
  // React.lazy keeps a failed import, so resetting would fail again; only a reload refetches it.
  const chunkFailed = isChunkLoadError(error)

  const handleRetry = () => {
    setCrashCount((c) => c + 1)
    resetErrorBoundary()
  }

  const handleReload = () => {
    window.location.reload()
  }

  const message = (
    <MessageBar intent="error" layout="multiline" style={{ maxWidth: '100%' }}>
      <MessageBarBody style={{ overflowWrap: 'anywhere' }}>
        Something went wrong: {error instanceof Error ? error.message : 'Unknown error'}
        {chunkFailed && (
          <>
            {' '}This part of PyRIT could not be downloaded, which can happen after an update or
            while the server is unreachable. Reloading the page will discard unsaved work.
          </>
        )}
      </MessageBarBody>
    </MessageBar>
  )

  const actions = (
    <>
      <Button appearance="primary" onClick={chunkFailed ? handleReload : handleRetry}>
        {chunkFailed ? 'Reload page' : 'Try again'}
      </Button>
      {!chunkFailed && crashCount > 1 && (
        <Button appearance="secondary" onClick={handleReload}>
          Reload page
        </Button>
      )}
    </>
  )

  if (variant === 'dialog') {
    return (
      <Dialog open modalType="alert" onOpenChange={(_, data) => { if (!data.open) onDismiss?.() }}>
        <DialogSurface data-testid="error-boundary-fallback">
          <DialogBody>
            <DialogTitle>Something went wrong</DialogTitle>
            <DialogContent>{message}</DialogContent>
            <DialogActions>
              {actions}
              {onDismiss && (
                <Button appearance="secondary" onClick={onDismiss}>
                  Close
                </Button>
              )}
            </DialogActions>
          </DialogBody>
        </DialogSurface>
      </Dialog>
    )
  }

  return (
    <div
      data-testid="error-boundary-fallback"
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        height: variant === 'page' ? '100vh' : undefined,
        gap: tokens.spacingVerticalL,
        padding: tokens.spacingHorizontalXXL,
      }}
    >
      {message}

      <div style={{ display: 'flex', gap: tokens.spacingHorizontalM }}>
        {actions}
      </div>
    </div>
  )
}

interface ErrorBoundaryProps {
  children: ReactNode
  variant?: ErrorBoundaryVariant
  /** Clears the error when any of these change, for example when the user opens another screen. */
  resetKeys?: unknown[]
  /** For the 'dialog' variant: closes the dialog that failed. */
  onDismiss?: () => void
}

export function ErrorBoundary({ children, variant = 'page', resetKeys, onDismiss }: ErrorBoundaryProps) {
  return (
    <ReactErrorBoundary
      fallbackRender={(props) => <ErrorFallback {...props} variant={variant} onDismiss={onDismiss} />}
      resetKeys={resetKeys}
      onError={(error, info) => {
        console.error('[ErrorBoundary] Caught render error:', error, info.componentStack)
      }}
    >
      {children}
    </ReactErrorBoundary>
  )
}
