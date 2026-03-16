import { MessageBar, MessageBarBody } from '@fluentui/react-components'
import type { ConnectionStatus } from '../hooks/useConnectionHealth'

const intentMap: Record<ConnectionStatus, 'error' | 'warning' | 'success'> = {
  disconnected: 'error',
  degraded: 'warning',
  connected: 'success',
}

const messageMap: Record<ConnectionStatus, string> = {
  disconnected: 'Unable to reach the PyRIT backend. Check that the server is running. Retrying…',
  degraded: 'Connection to the backend is unstable.',
  connected: 'Reconnected to the backend.',
}

interface ConnectionBannerProps {
  status: ConnectionStatus
}

export function ConnectionBanner({ status }: ConnectionBannerProps) {
  return (
    <div data-testid="connection-banner" style={{ position: 'sticky', top: 0, zIndex: 1000 }}>
      <MessageBar intent={intentMap[status]}>
        <MessageBarBody>{messageMap[status]}</MessageBarBody>
      </MessageBar>
    </div>
  )
}
