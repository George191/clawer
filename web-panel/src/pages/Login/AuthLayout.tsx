import React from 'react';
import { Typography } from 'antd';
import { LockOutlined, MailOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import { useLocation } from 'react-router-dom';

type BackgroundProps = { onUnavailable: () => void; onReady?: () => void };

const BackgroundLoadFailure = ({ onUnavailable }: BackgroundProps) => {
  React.useEffect(onUnavailable, [onUnavailable]);
  return null;
};
const CinematicSpace = React.lazy<React.ComponentType<BackgroundProps>>(() => import('./CinematicSpace').catch(() => ({ default: BackgroundLoadFailure })));
const SpaceBackground = React.lazy<React.ComponentType>(() => import('./SpaceBackground').catch(() => ({ default: () => null })));

const AuthBackground = () => {
  const [mounted, setMounted] = React.useState(false);
  const [ready, setReady] = React.useState(false);
  const [failed, setFailed] = React.useState(false);
  const active = React.useRef(true);
  const backgroundReady = React.useCallback(() => { if (active.current) setReady(true); }, []);
  const backgroundUnavailable = React.useCallback(() => {
    if (!active.current) return;
    setFailed(true);
    setReady(true);
  }, []);
  React.useEffect(() => {
    active.current = true;
    let secondFrame = 0;
    const firstFrame = requestAnimationFrame(() => {
      secondFrame = requestAnimationFrame(() => { if (active.current) setMounted(true); });
    });
    return () => {
      active.current = false;
      cancelAnimationFrame(firstFrame);
      cancelAnimationFrame(secondFrame);
    };
  }, []);

  return <>
    <div className={`sp-login-background-loader${ready ? ' is-ready' : ''}`} aria-hidden="true">
      <svg viewBox="0 0 160 160" fill="none">
        <circle cx="80" cy="80" r="56" stroke="#386dca" strokeOpacity=".35" strokeDasharray="2 6" />
        <g className="sp-login-loader-earth">
          <circle cx="80" cy="80" r="34" stroke="#2c6fff" strokeOpacity=".18" strokeWidth="6" />
          <circle cx="80" cy="80" r="29" fill="#091e35" stroke="#528dff" strokeOpacity=".7" />
          <ellipse cx="80" cy="80" rx="12" ry="29" stroke="#3e74c7" strokeOpacity=".5" />
          <path d="M53 71Q80 85 107 71M53 90Q80 77 107 90" stroke="#3e74c7" strokeOpacity=".45" />
          <path d="M65 59L78 56L85 63L81 72L74 73L70 83L64 78L66 70ZM84 80L94 83L97 91L90 102L84 96L86 89L80 85Z" fill="#3268aa" fillOpacity=".7" />
        </g>
        <g className="sp-login-loader-orbit">
          <g transform="translate(136 80) rotate(-20)">
            <circle r="13" fill="#2c6fff" fillOpacity=".1" stroke="#2c6fff" strokeOpacity=".4" />
            <path d="M-13-4H-6V4H-13ZM6-4H13V4H6Z" fill="#285cb4" stroke="#89b5ff" strokeWidth=".7" />
            <path d="M-6 0H6M0-7V-3" stroke="#a8caff" />
            <rect x="-3" y="-5" width="6" height="10" rx="1" fill="#b5d1f5" />
          </g>
        </g>
      </svg>
      <span>Preparing orbital view</span>
    </div>
    {mounted && <React.Suspense fallback={null}>{failed ? <SpaceBackground /> : <CinematicSpace onUnavailable={backgroundUnavailable} onReady={backgroundReady} />}</React.Suspense>}
  </>;
};

const AuthLayout: React.FC<React.PropsWithChildren> = ({ children }) => {
  const isRecovery = useLocation().pathname === '/reset-password';
  const [compact, setCompact] = React.useState(() => window.matchMedia('(max-width: 960px)').matches);
  React.useEffect(() => {
    const query = window.matchMedia('(max-width: 960px)');
    const change = () => setCompact(query.matches);
    query.addEventListener('change', change);
    return () => query.removeEventListener('change', change);
  }, []);

  return (
    <div className="sp-login">
      {!compact && <AuthBackground />}
      <div className="sp-login-graph" aria-hidden="true">
        <div className="sp-login-copy" key={isRecovery ? 'recovery-copy' : 'login-copy'}>
          <Typography.Text className="sp-login-brand">ASIRAL HELIO</Typography.Text>
          <Typography.Title level={1}>{isRecovery ? 'Secure Identity Recovery' : 'Unified Data Intelligence Platform'}</Typography.Title>
          <Typography.Paragraph>{isRecovery ? 'Restore access to your unified data intelligence workspace through an auditable verification flow.' : 'Connect collection, lakehouse, ETL, AI and geospatial data in one observable and governed platform.'}</Typography.Paragraph>
          {!isRecovery && <div className="sp-login-domain-list"><span>Data Collection</span><span>Data Lake</span><span>ETL</span><span>GIS Cockpit</span><span>AI Workspace</span></div>}
          <div className="sp-login-capabilities">
            {isRecovery ? <><span><SafetyCertificateOutlined /> Time-limited verification</span><span><LockOutlined /> Password-safe recovery</span><span><MailOutlined /> Enterprise email identity</span></> : <><span><SafetyCertificateOutlined /> Tenant isolation</span><span><LockOutlined /> Auditable operations</span><span><MailOutlined /> Enterprise identity</span></>}
          </div>
        </div>
      </div>
      <div className={`sp-login-panel${isRecovery ? ' sp-reset-password' : ''}`}>
        {children}
      </div>
    </div>
  );
};

export default AuthLayout;
