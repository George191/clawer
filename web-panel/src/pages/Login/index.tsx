import React, { useState } from 'react';
import { Alert, App, Button, Checkbox, Form, Input, Typography } from 'antd';
import { LockOutlined, MailOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import { Link, useNavigate } from 'react-router-dom';
import './login.css';

const Login: React.FC = () => {
  const navigate = useNavigate();
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
      if (!response.ok) throw new Error('Incorrect account or password. Please try again.');
      const data = await response.json() as { access_token?: string; refresh_token?: string };
      if (data.access_token) localStorage.setItem('access_token', data.access_token);
      if (data.refresh_token) localStorage.setItem('refresh_token', data.refresh_token);
      message.success('Signed in successfully');
      navigate('/');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign-in failed. Please try again.');
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
          {error && <Alert message={error} type="error" showIcon style={{ marginBottom: 18 }} />}
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
