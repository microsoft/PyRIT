import { render, screen } from '@testing-library/react'
import { ConnectionBanner } from './ConnectionBanner'

describe('ConnectionBanner', () => {
  it('renders warning banner when degraded', () => {
    render(<ConnectionBanner status="degraded" />)

    const banner = screen.getByTestId('connection-banner')
    expect(banner).toBeInTheDocument()
    expect(screen.getByText(/unstable/i)).toBeInTheDocument()
  })

  it('renders error banner when disconnected', () => {
    render(<ConnectionBanner status="disconnected" />)

    const banner = screen.getByTestId('connection-banner')
    expect(banner).toBeInTheDocument()
    expect(screen.getByText(/unable to reach/i)).toBeInTheDocument()
  })

  it('renders success banner when connected', () => {
    render(<ConnectionBanner status="connected" />)

    const banner = screen.getByTestId('connection-banner')
    expect(banner).toBeInTheDocument()
    expect(screen.getByText(/reconnected/i)).toBeInTheDocument()
  })
})
