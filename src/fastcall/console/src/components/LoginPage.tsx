import { FormEvent, useState } from 'react';
import { api, ApiError } from '../api/client';
import { ConsoleSession } from '../api/types';

export default function LoginPage({ onLogin }: { onLogin: (session: ConsoleSession) => void }) {
  const [actorId, setActorId] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError('');
    try {
      const session = await api<ConsoleSession>('/login', {
        method: 'POST',
        body: JSON.stringify({ actor_id: actorId, password }),
      });
      onLogin(session);
    } catch (err) {
      setError(err instanceof ApiError ? '账号或密码不正确' : '登录服务不可用');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-page">
      <form className="login-card" onSubmit={submit}>
        <div className="login-brand"><span className="brand-mark">R</span><div><h1>Robot Config</h1><p>配置管理控制台</p></div></div>
        <label>账号<input autoFocus autoComplete="username" value={actorId} onChange={event => setActorId(event.target.value)} required /></label>
        <label>密码<input type="password" autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)} required /></label>
        {error && <div className="form-error" role="alert">{error}</div>}
        <button className="button primary login-button" disabled={submitting}>{submitting ? '登录中…' : '登录'}</button>
      </form>
    </main>
  );
}
