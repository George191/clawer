import React, { useEffect, useState } from 'react';
import { Alert, App, Button, Form, Input, Typography } from 'antd';
import {
  ArrowLeftOutlined,
  LockOutlined,
  MailOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import './login.css';

type RecoveryStep = 'email' | 'code' | 'password';

const readError = async (response: Response, fallback: string) => {
  try {
    const body = await response.json() as { detail?: string; message?: string };
    return body.detail || body.message || fallback;
  } catch {
    return fallback;
  }
};

const ResetPassword: React.FC = () => {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const legacyToken = searchParams.get('token') || '';
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [step, setStep] = useState<RecoveryStep>(legacyToken ? 'password' : 'email');
  const [email, setEmail] = useState('');
  const [challengeToken, setChallengeToken] = useState('');
  const [resetToken, setResetToken] = useState('');
  const [countdown, setCountdown] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (countdown <= 0) return;
    const timer = window.setInterval(() => setCountdown((value) => Math.max(0, value - 1)), 1000);
    return () => window.clearInterval(timer);
  }, [countdown]);

  const sendCode = async (targetEmail: string) => {
    setLoading(true);
    setError('');
    try {
      const response = await fetch('/api/v1/password-recovery/code', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: targetEmail }),
      });
      if (!response.ok) throw new Error(await readError(response, 'Unable to send the verification code.'));
      const data = await response.json() as { challenge_token: string; expires_in?: number };
      setEmail(targetEmail);
      setChallengeToken(data.challenge_token);
      setCountdown(60);
      setStep('code');
      form.setFieldsValue({ code: '' });
      message.success('If the account exists, a verification code has been sent.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to send the verification code.');
    } finally {
      setLoading(false);
    }
  };

  const verifyCode = async (values: { code: string }) => {
    setLoading(true);
    setError('');
    try {
      const response = await fetch('/api/v1/password-recovery/code/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ challenge_token: challengeToken, code: values.code }),
      });
      if (!response.ok) throw new Error(await readError(response, 'Invalid or expired verification code.'));
      const data = await response.json() as { reset_token: string };
      setResetToken(data.reset_token);
      setStep('password');
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Invalid or expired verification code.');
    } finally {
      setLoading(false);
    }
  };

  const updatePassword = async (values: { password: string }) => {
    setLoading(true);
    setError('');
    try {
      const response = await fetch(legacyToken ? '/api/v1/reset-password/' : '/api/v1/reset-password/code', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(legacyToken
          ? { token: legacyToken, new_password: values.password }
          : { reset_token: resetToken, new_password: values.password }),
      });
      if (!response.ok) throw new Error(await readError(response, 'Unable to update your password.'));
      message.success('Password updated. Sign in with your new password.');
      navigate('/login', { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to update your password.');
    } finally {
      setLoading(false);
    }
  };

  const stepCopy = {
    email: ['Recover your account', 'Enter your work email to receive a secure verification code.'],
    code: ['Verify your identity', `Enter the six-digit code sent to ${email}.`],
    password: ['Create a new password', 'Use at least 8 characters for your new account password.'],
  }[step];

  return (
        <div className="sp-login-form-wrap">
          <div className="sp-login-form-brand">
            <span className="sp-login-form-logo"><img src="/astral-helio-mark.svg" alt="" /><b>ASIRAL HELIO</b></span>
            <Typography.Text className="sp-login-eyebrow">IDENTITY RECOVERY</Typography.Text>
          </div>
          <div className="sp-recovery-progress" aria-label={`Step ${step === 'email' ? 1 : step === 'code' ? 2 : 3} of 3`}>
            <span className="is-active" /><span className={step !== 'email' ? 'is-active' : ''} /><span className={step === 'password' ? 'is-active' : ''} />
          </div>
          <Typography.Title level={2}>{stepCopy[0]}</Typography.Title>
          <Typography.Paragraph type="secondary" className="sp-login-form-subtitle">{stepCopy[1]}</Typography.Paragraph>
          {error && <Alert message={error} type="error" showIcon style={{ marginBottom: 18 }} />}

          {step === 'email' && (
            <Form form={form} layout="vertical" onFinish={(values: { email: string }) => sendCode(values.email)}>
              <Form.Item label="Work email" name="email" rules={[{ required: true, message: 'Enter your email address' }, { type: 'email', message: 'Enter a valid email address' }]}>
                <Input size="large" prefix={<MailOutlined />} placeholder="name@company.com" autoComplete="email" autoFocus />
              </Form.Item>
              <Button type="primary" htmlType="submit" size="large" block loading={loading}>Send verification code</Button>
            </Form>
          )}

          {step === 'code' && (
            <Form form={form} layout="vertical" onFinish={verifyCode}>
              <Form.Item label="Verification code" name="code" rules={[{ required: true, message: 'Enter the verification code' }, { pattern: /^\d{6}$/, message: 'Enter the six-digit code' }]}>
                <Input className="sp-verification-code" size="large" inputMode="numeric" maxLength={6} placeholder="000000" autoComplete="one-time-code" autoFocus />
              </Form.Item>
              <Button type="primary" htmlType="submit" size="large" block loading={loading}>Verify code</Button>
              <Button type="link" block disabled={countdown > 0 || loading} onClick={() => sendCode(email)}>
                {countdown > 0 ? `Resend available in ${countdown}s` : 'Resend verification code'}
              </Button>
            </Form>
          )}

          {step === 'password' && (
            <Form form={form} layout="vertical" onFinish={updatePassword}>
              <Form.Item label="New password" name="password" rules={[{ required: true, message: 'Enter a new password' }, { min: 8, message: 'Use at least 8 characters' }]}>
                <Input.Password size="large" prefix={<LockOutlined />} placeholder="Enter a new password" autoComplete="new-password" autoFocus />
              </Form.Item>
              <Form.Item label="Confirm new password" name="confirmPassword" dependencies={['password']} rules={[{ required: true, message: 'Confirm your new password' }, ({ getFieldValue }) => ({ validator(_, value) { return !value || getFieldValue('password') === value ? Promise.resolve() : Promise.reject(new Error('Passwords do not match')); } })]}>
                <Input.Password size="large" prefix={<LockOutlined />} placeholder="Confirm your new password" autoComplete="new-password" />
              </Form.Item>
              <Button type="primary" htmlType="submit" size="large" block loading={loading}>Update password</Button>
            </Form>
          )}

          <Link className="sp-recovery-back" to="/login"><ArrowLeftOutlined /> Back to sign in</Link>
          <div className="sp-login-security-note"><SafetyCertificateOutlined /><span>{legacyToken ? 'Secure recovery links are time limited' : 'Verification codes expire after 10 minutes'}</span></div>
        </div>
  );
};

export default ResetPassword;
