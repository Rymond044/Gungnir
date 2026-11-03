import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router'
import Home from './app/Home'
import Layout from './app/Layout'
import Analysis from './pages/Analysis'
import Anomalies from './pages/Anomalies'
import Anomaly from './pages/Anomaly'
import Cause from './pages/Cause'
import Collectors from './pages/Collectors'
import Knowledge from './pages/Knowledge'
import NotFound from './pages/NotFound'
import Overview from './pages/Overview'
import './styles/global.css'

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: true } },
})

const router = createBrowserRouter([
  { path: '/', element: <Home /> },
  {
    path: '/hosts/:host',
    element: <Layout />,
    children: [
      { index: true, element: <Overview /> },
      { path: 'anomalies', element: <Anomalies /> },
      { path: 'anomalies/:id', element: <Anomaly /> },
      { path: 'analysis', element: <Analysis /> },
      { path: 'collectors', element: <Collectors /> },
      { path: 'knowledge', element: <Knowledge /> },
      { path: 'knowledge/:cause', element: <Cause /> },
      { path: '*', element: <NotFound /> },
    ],
  },
  { path: '*', element: <NotFound /> },
])

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
)
