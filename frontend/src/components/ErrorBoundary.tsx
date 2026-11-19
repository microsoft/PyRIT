import { useState } from 'react'
import { ErrorBoundary as ReactErrorBoundary } from 'react-error-boundary'
import type { FallbackProps } from 'react-error-boundary'
import type { ReactNode } from 'react'
import { Button, MessageBar, MessageBarBody, tokens } from '@fluentui/react-components'
import { isChunkLoadError } from '@/utils/chunkReload'

function ErrorFallback({ error, resetErrorBoundary }: FallbackProps) {
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

  return (
    <div
      data-testid="error-boundary-fallback"
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        height: '100vh',
        gap: tokens.spacingVerticalL,
        padding: tokens.spacingHorizontalXXL,
      }}
    >
      <MessageBar intent="error">
        <MessageBarBody>
          Something went wrong: {error instanceof Error ? error.message : 'Unknown error'}
        </MessageBarBody>
      </MessageBar>

      <div style={{ display: 'flex', gap: tokens.spacingHorizontalM }}>
        <Button appearance="primary" onClick={chunkFailed ? handleReload : handleRetry}>
          {chunkFailed ? 'Reload page' : 'Try again'}
        </Button>
        {!chunkFailed && crashCount > 1 && (
          <Button appearance="secondary" onClick={handleReload}>
            Reload page
          </Button>
        )}
      </div>
    </div>
  )
}

interface ErrorBoundaryProps {
  children: ReactNode
}

export function ErrorBoundary({ children }: ErrorBoundaryProps) {
  return (
    <ReactErrorBoundary
      FallbackComponent={ErrorFallback}
      onError={(error, info) => {
        console.error('[ErrorBoundary] Caught render error:', error, info.componentStack)
      }}
    >
      {children}
    </ReactErrorBoundary>
  )
}
