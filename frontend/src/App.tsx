import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import { AppShell } from './components/layout/AppShell'
import { OverviewPage } from './pages/OverviewPage'
import { CaseWorkspacePage } from './pages/CaseWorkspacePage'
import { EscalationsPage } from './pages/EscalationsPage'

const client = new QueryClient({
  defaultOptions: {
    queries: { refetchOnWindowFocus: false, refetchIntervalInBackground: false, retry: 1 },
  },
})

const router = createBrowserRouter([
  {
    path: '/',
    element: <AppShell />,
    children: [
      { index: true, element: <OverviewPage /> },
      { path: 'cases/:caseId', element: <CaseWorkspacePage /> },
      { path: 'escalations', element: <EscalationsPage /> },
    ],
  },
])

export default function App() {
  return (
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  )
}
