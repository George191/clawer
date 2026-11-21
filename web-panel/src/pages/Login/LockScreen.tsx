import { useEffect, useRef, useState } from 'react';
import { Button, Form, Input, Typography, message } from '@/components/ui';
import { LockOutlined } from '@/components/ui/icons';
import { clearSession, getRefreshToken, getSessionState, unlockSession } from '../../services/session';
import { apiUrl } from '../../services/endpoints';

export default function LockScreen() {
  const [loading, setLoading] = useState(false);
  const pending = useRef<AbortController>();
  useEffect(() => () => pending.current?.abort(), []);
  const unlock = async ({ password }: { password: string }) => {
    if (pending.current) return;
    const refresh = getRefreshToken();
    if (!refresh) { clearSession(); return; }
    const request = new AbortController(); pending.current = request;
    const current = () => !request.signal.aborted && getRefreshToken() === refresh && getSessionState().authenticated && getSessionState().locked;
    setLoading(true);
    try {
      const response = await fetch(apiUrl('/api/v1/login/unlock'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ refresh_token: refresh, password }), signal: request.signal });
      const body = await response.json() as { access_token?: string; data?: { access_token?: string }; detail?: string; errors?: string };
      if (!current()) return;
      if (!response.ok) {
        const detail = body.errors || body.detail;
        if ([401, 403].includes(response.status) && detail !== 'Incorrect password') { clearSession(); return; }
        throw new Error(detail === 'Incorrect password' ? 'Incorrect password. Your session is still locked.' : 'Unable to unlock. Please try again.');
      }
      const token = body.data?.access_token || body.access_token;
      if (!token || !unlockSession(token, refresh)) throw new Error('Your session has changed. Sign in again.');
    } catch (cause) { if (current()) message.error({ content: 'Unable to unlock workspace', description: cause instanceof TypeError ? 'Unable to connect. Your session is still locked.' : cause instanceof Error ? cause.message : 'Unable to unlock. Please try again.', className: 'explorer-toast' }); }
    finally { if (pending.current === request) pending.current = undefined; if (!request.signal.aborted) setLoading(false); }
  };
  return <section className="sp-login-form-wrap" aria-label="Session locked">
    <Typography.Title level={2}><LockOutlined aria-hidden="true" /> Session locked</Typography.Title>
    <Typography.Paragraph type="secondary">Your workspace was locked after 30 minutes of inactivity. Enter your password to continue. Signing in again is required after 8 hours.</Typography.Paragraph>
    <Form layout="vertical" onFinish={unlock}>
      <Form.Item name="password" label="Password" rules={[{ required: true, message: 'Enter your password' }]}><Input.Password autoFocus autoComplete="current-password" disabled={loading} /></Form.Item>
      <Button type="primary" htmlType="submit" loading={loading} block>Unlock workspace</Button>
    </Form>
    <Button type="link" disabled={loading} onClick={clearSession} style={{ marginTop: 14 }}>Sign out and use another account</Button>
  </section>;
}
