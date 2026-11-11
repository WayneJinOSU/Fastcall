import { useEffect, useState } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { api, setCsrfToken } from './api/client';
import { ConsoleSession } from './api/types';
import AppShell from './components/AppShell';
import LoginPage from './components/LoginPage';
import AppListPage from './features/apps/AppListPage';
import AppEditorPage from './features/editor/AppEditorPage';

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

  return (
    <AppShell>
      <Routes>
        <Route path="/apps" element={<AppListPage />} />
        <Route path="/apps/:appId/edit" element={<AppEditorPage />} />
        <Route path="*" element={<Navigate to="/apps" replace />} />
      </Routes>
    </AppShell>
  );
}
