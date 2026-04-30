import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Toaster } from 'sonner'
import { TooltipProvider } from '@/components/ui/tooltip'
import App from './App'
import './tailwind.css'
import './style.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30 * 1000,
      retry: 1,
      refetchOnWindowFocus: true,
    },
  },
})

ReactDOM.createRoot(document.getElementById('app')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <TooltipProvider delayDuration={300}>
          <App />
          <Toaster
            position="bottom-right"
            toastOptions={{
              style: {
                background: 'var(--surface)',
                color: 'var(--ink)',
                border: '1px solid var(--line)',
                borderRadius: 'var(--radius-md, 10px)',
                fontFamily: 'inherit',
              },
            }}
          />
        </TooltipProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
)
