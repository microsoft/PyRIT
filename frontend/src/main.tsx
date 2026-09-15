import React from 'react'
import ReactDOM from 'react-dom/client'

import AppRouter from './AppRouter'
import { AuthProvider } from './auth/AuthProvider'
import { ThemeProvider } from './hooks/useTheme'
import './styles/global.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ThemeProvider>
      <AuthProvider>
        <AppRouter />
      </AuthProvider>
    </ThemeProvider>
  </React.StrictMode>,
)
