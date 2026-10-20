import { useState } from 'react'
import { createBrowserRouter } from 'react-router'
import { RouterProvider } from 'react-router/dom'

import App from './App'

export default function AppRouter() {
  const [router] = useState(() => createBrowserRouter([
    {
      path: '*',
      element: <App />,
    },
  ]))

  return <RouterProvider router={router} />
}
