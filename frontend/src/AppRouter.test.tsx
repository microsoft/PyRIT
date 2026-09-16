import { render, screen } from '@testing-library/react'

const mockCreateBrowserRouter = jest.fn().mockReturnValue({})

jest.mock('react-router', () => ({
  createBrowserRouter: mockCreateBrowserRouter,
  RouterProvider: () => <div data-testid="router-provider" />,
}))

jest.mock('./App', () => () => <div data-testid="app" />)

import AppRouter from './AppRouter'

describe('AppRouter', () => {
  beforeEach(() => {
    mockCreateBrowserRouter.mockClear()
  })

  it('creates the browser router only when the authenticated child tree mounts', () => {
    expect(mockCreateBrowserRouter).not.toHaveBeenCalled()

    render(<AppRouter />)

    expect(mockCreateBrowserRouter).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId('router-provider')).toBeInTheDocument()
  })
})
