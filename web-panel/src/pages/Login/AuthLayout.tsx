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
  const [orbitDelay] = React.useState(() => {
    const epoch = Number.parseFloat(document.documentElement.style.getPropertyValue('--sp-auth-orbit-epoch'));
    return Number.isFinite(epoch) ? -Math.max(0, performance.now() - epoch) : 0;
  });
  const [mounted, setMounted] = React.useState(false);
  const [ready, setReady] = React.useState(false);
  const [failed, setFailed] = React.useState(false);
  const active = React.useRef(true);
  const orbitRef = React.useRef<HTMLImageElement>(null);
  React.useLayoutEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const epoch = Number.parseFloat(document.documentElement.style.getPropertyValue('--sp-auth-orbit-epoch'));
    const animation = orbitRef.current?.getAnimations()[0];
    if (!Number.isFinite(epoch) || !animation?.effect) return;
    const delay = animation.effect.getTiming().delay;
    if (typeof delay === 'number') animation.startTime = epoch - delay;
  }, []);
  // LOADING_QA_START
  React.useEffect(() => {
    if (ready) return;
    const gaps: number[] = [], tasks: number[] = [], errors: number[] = [];
    let frame = 0, previous = 0;
    const observer = new PerformanceObserver(list => { for (const entry of list.getEntries()) tasks.push(entry.duration); });
    observer.observe({ type: 'longtask' });
    const tick = (now: number) => {
      if (previous) gaps.push(now - previous);
      previous = now;
      const orbit = document.querySelector('.sp-login-background-loader .sp-auth-loading-satellite')?.getAnimations()[0];
      const epoch = Number.parseFloat(document.documentElement.style.getPropertyValue('--sp-auth-orbit-epoch'));
      if (orbit && typeof orbit.currentTime === 'number' && Number.isFinite(epoch)) errors.push(Math.abs(orbit.currentTime - Number(orbit.effect!.getTiming().delay) - (now - epoch)));
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(frame); observer.disconnect();
      const sorted = [...gaps].sort((a,b) => a-b);
      document.documentElement.dataset.loadingPerf = JSON.stringify({ samples:gaps.length, median:sorted[Math.floor(sorted.length*.5)], p95:sorted[Math.floor(sorted.length*.95)], max:sorted[sorted.length-1], tasks, phaseErrorMax:Math.max(0,...errors) });
    };
  }, [ready]);
  // LOADING_QA_END
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
    <div className={`sp-login-background-loader${ready ? ' is-ready' : ''}`} role="status" aria-live="polite">
      <div className="sp-auth-loading-stage" aria-hidden="true">
        <img className="sp-auth-loading-earth" src="/space/auth-loading.svg" width="148" height="148" alt="" />
        <img ref={orbitRef} className="sp-auth-loading-satellite" src="/space/auth-loading-satellite.svg" width="148" height="148" alt="" style={{ animationDelay: `${orbitDelay}ms` }} />
      </div>
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
