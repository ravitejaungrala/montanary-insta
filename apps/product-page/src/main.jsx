import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App.jsx'
import './index.css'

import { NotificationProvider } from './context/NotificationContext.jsx'

// StrictMode runs every render + effect TWICE in development to surface
// impure components and missing cleanup. That doubling was making the
// local dev experience feel sluggish (cursor freezes, slow page-tab
// switches) on top of remote backend latency. Production isn't affected
// by StrictMode at all, so removing it locally has zero impact on the
// deployed app — only on the development double-render overhead.
//
// To re-enable for an effects-bug investigation:
//   VITE_STRICT_MODE=true npm run dev
const Root = import.meta.env.VITE_STRICT_MODE === 'true'
  ? React.StrictMode
  : React.Fragment

ReactDOM.createRoot(document.getElementById('root')).render(
  <Root>
    <BrowserRouter>
      <NotificationProvider>
        <App />
      </NotificationProvider>
    </BrowserRouter>
  </Root>,
)
