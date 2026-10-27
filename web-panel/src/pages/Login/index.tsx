import React, { useState } from 'react';
import { Alert, App, Button, Checkbox, Form, Input, Typography } from 'antd';
import { LockOutlined, MailOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import './login.css';

const Login: React.FC = () => {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const submit = async (values: { account: string; password: string }) => {
    setLoading(true);
    setError('');
    try {
      const response = await fetch('/api/v1/login/token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({ username: values.account, password: values.password }),
      });
      if (!response.ok) {
        let detail: unknown;
        try {
          const body = await response.json() as { errors?: unknown; detail?: unknown };
          detail = body.errors ?? body.detail;
        } catch { /* Proxy errors may not contain JSON. */ }
        if (response.status === 401) {
          throw new Error(detail === 'Inactive user'
            ? 'This account is inactive. Contact your administrator.'
            : 'Incorrect account or password. Please try again.');
        }
        throw new Error(response.status >= 500
          ? 'Sign-in service unavailable. Please try again later.'
          : 'Unable to sign in. Please try again.');
      }
      const data = await response.json() as { access_token?: string; refresh_token?: string };
      if (typeof data.access_token !== 'string' || !data.access_token) {
        throw new Error('Sign-in failed: no access token was returned. Please try again.');
      }
      localStorage.setItem('access_token', data.access_token);
      if (typeof data.refresh_token === 'string' && data.refresh_token) localStorage.setItem('refresh_token', data.refresh_token);
      else localStorage.removeItem('refresh_token');
      let next = '/';
      const target = searchParams.get('next');
      if (target?.startsWith('/') && !target.startsWith('//') && !target.includes('\\')) {
        const url = new URL(target, window.location.origin);
        if (url.origin === window.location.origin && !['/login', '/reset-password'].includes(url.pathname)) {
          next = url.pathname + url.search + url.hash;
        }
      }
      message.success('Signed in successfully');
      navigate(next, { replace: true });
    } catch (err) {
      setError(err instanceof TypeError ? 'Unable to connect. Please try again.'
        : err instanceof Error ? err.message : 'Sign-in failed. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  return (
        <div className="sp-login-form-wrap">
          <div className="sp-login-form-brand">
            <span className="sp-login-form-logo"><img src="/astral-helio-mark.svg" alt="" /><b>ASIRAL HELIO</b></span>
            <Typography.Text className="sp-login-eyebrow">SECURE PLATFORM ACCESS</Typography.Text>
          </div>
          <Typography.Title level={2}>Sign in to Asiral Helio</Typography.Title>
          <Typography.Paragraph type="secondary" className="sp-login-form-subtitle">Access your unified data, compute and geospatial workspace.</Typography.Paragraph>
          {error && <Alert className="sp-login-error" message={<span title={error}>{error}</span>} type="error" showIcon style={{ marginBottom: 18 }} />}
          <Form layout="vertical" onFinish={submit} initialValues={{ account: '', remember: true }}>
            <Form.Item label="Email or phone" name="account" rules={[{ required: true, message: 'Enter your email or phone number' }]}>
              <Input size="large" prefix={<MailOutlined />} placeholder="name@company.com" autoComplete="username" />
            </Form.Item>
            <Form.Item label="Password" name="password" rules={[{ required: true, message: 'Enter your password' }]}>
              <Input.Password size="large" prefix={<LockOutlined />} placeholder="Enter your password" autoComplete="current-password" />
            </Form.Item>
            <div className="sp-login-options"><Form.Item name="remember" valuePropName="checked" noStyle><Checkbox>Keep me signed in</Checkbox></Form.Item><Link to="/reset-password">Forgot password?</Link></div>
            <Button type="primary" htmlType="submit" size="large" block loading={loading}>Sign in</Button>
            <div className="sp-login-divider"><span>OR</span></div>
            <Button size="large" block className="sp-sso-button" onClick={() => message.info('Contact your administrator to configure enterprise SSO')}>Continue with enterprise SSO</Button>
          </Form>
          <div className="sp-login-security-note"><SafetyCertificateOutlined /><span>Protected by tenant isolation and audited access controls</span></div>
          <Typography.Text type="secondary" className="sp-login-terms">By continuing, you agree to the Terms of Service and Privacy Policy.</Typography.Text>
        </div>
  );
};

export default Login;
