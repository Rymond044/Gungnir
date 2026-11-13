import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, Navigate, RouterProvider } from 'react-router'
import Crash from './app/Crash'
import Home from './app/Home'
import Layout from './app/Layout'
import Audit from './pages/Audit'
import Anomalies from './pages/Anomalies'
import Anomaly from './pages/Anomaly'
import Cause from './pages/Cause'
import Collectors from './pages/Collectors'
import Incident from './pages/Incident'
import Incidents from './pages/Incidents'
import Knowledge from './pages/Knowledge'
import Metric from './pages/Metric'
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
    errorElement: <Crash />,
    children: [
      {
        errorElement: <Crash />,
        children: [
          { index: true, element: <Overview /> },
          { path: 'incidents', element: <Incidents /> },
          { path: 'incidents/:id', element: <Incident /> },
          { path: 'anomalies', element: <Anomalies /> },
          { path: 'anomalies/:id', element: <Anomaly /> },
          { path: 'audit', element: <Audit /> },
          { path: 'analysis', element: <Navigate to="../audit" relative="path" replace /> },
          { path: 'collectors', element: <Collectors /> },
          { path: 'metric', element: <Metric /> },
          { path: 'knowledge', element: <Knowledge /> },
          { path: 'knowledge/:cause', element: <Cause /> },
          { path: '*', element: <NotFound /> },
        ],
      },
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
