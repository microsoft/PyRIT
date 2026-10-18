import { useState } from 'react'
import { createBrowserRouter, RouterProvider } from 'react-router'

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
