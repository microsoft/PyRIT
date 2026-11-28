import React from 'react'
import ReactDOM from 'react-dom/client'

import AppRouter from './AppRouter'
import { AuthProvider } from './auth/AuthProvider'
import { CompatibilityGate } from './components/CompatibilityGate'
import './styles/global.css'

const rootElement = document.getElementById('root')
if (!rootElement) {
  throw new Error('Root element not found')
}

ReactDOM.createRoot(rootElement).render(
  <React.StrictMode>
    <AuthProvider>
      <CompatibilityGate>
        <AppRouter />
      </CompatibilityGate>
    </AuthProvider>
  </React.StrictMode>,
)
