import { useEffect, useState } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { api, setCsrfToken } from './api/client';
import { ConsoleSession } from './api/types';
import AppShell from './components/AppShell';
import LoginPage from './components/LoginPage';
import AppListPage from './features/apps/AppListPage';
import AppEditorPage from './features/editor/AppEditorPage';
import TaskListPage from './features/tasks/TaskListPage';
import TaskDetailPage from './features/tasks/TaskDetailPage';
import CallDetailPage from './features/calls/CallDetailPage';
import AgentReportsPage from './features/agent-reports/AgentReportsPage';
import MockCallPage from './features/mock-calls/MockCallPage';

export default function App() {
  const [session, setSession] = useState<ConsoleSession | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api<ConsoleSession>('/session')
      .then(value => {
        setCsrfToken(value.csrf_token);
        setSession(value);
      })
      .catch(() => setSession(null))
      .finally(() => setLoading(false));
    const unauthorized = () => {
      setCsrfToken('');
      setSession(null);
    };
    window.addEventListener('console:unauthorized', unauthorized);
    return () => window.removeEventListener('console:unauthorized', unauthorized);
  }, []);

  function login(value: ConsoleSession) {
    setCsrfToken(value.csrf_token);
    setSession(value);
  }

  if (loading) return <div className="page-loading">正在加载控制台…</div>;
  if (!session) return <LoginPage onLogin={login} />;

  return <AppShell actorId={session.actor_id}><Routes>
    <Route path="/tasks" element={<TaskListPage />} />
    <Route path="/tasks/:taskId" element={<TaskDetailPage />} />
    <Route path="/calls/:callId" element={<CallDetailPage />} />
    <Route path="/reports/agent" element={<AgentReportsPage />} />
    <Route path="/mock-calls" element={<MockCallPage />} />
    <Route path="/apps" element={<AppListPage />} />
    <Route path="/apps/:appId/edit" element={<AppEditorPage />} />
    <Route path="*" element={<Navigate to="/tasks" replace />} />
  </Routes></AppShell>;
}
