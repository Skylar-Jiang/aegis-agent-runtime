import {
  BrowserRouter,
  Navigate,
  Route,
  Routes,
  useLocation,
} from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import { Layout } from './components/Layout'
import { TasksPage } from './pages/TasksPage'
import { ApprovalsPage } from './pages/ApprovalsPage'
import { AuditPage } from './pages/AuditPage'
import { ExperimentsPage } from './pages/ExperimentsPage'
import { TaskGraphPage } from './pages/TaskGraphPage'
import { ConversationsPage } from './pages/ConversationsPage'
import { SecuritySettingsPage } from './pages/SecuritySettingsPage'
import { CoreWorkbench } from './features/core/CoreWorkbench'

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 5000 } },
})

function LandingPage() {
  const { search } = useLocation()
  const params = new URLSearchParams(search)
  if (params.has('conversation_id'))
    return <Navigate replace to={`/conversations${search}`} />
  if (params.has('task_id')) return <Navigate replace to={`/tasks${search}`} />
  return <CoreWorkbench />
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/" element={<LandingPage />} />
            <Route path="/conversations" element={<ConversationsPage />} />
            <Route path="/tasks" element={<TasksPage />} />
            <Route
              path="/settings/security"
              element={<SecuritySettingsPage />}
            />
            <Route path="/approvals" element={<ApprovalsPage />} />
            <Route path="/audit" element={<AuditPage />} />
            <Route path="/experiments" element={<ExperimentsPage />} />
            <Route path="/runtime" element={<TaskGraphPage />} />
            <Route path="/core" element={<CoreWorkbench />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
