import type { ReactNode } from 'react';
import { ThinkingOrb } from 'thinking-orbs';
import './loading.css';

export type LoadingState = 'working' | 'solving';

export function LoadingOrb({ state = 'working', size = 20, paused = false }: { state?: LoadingState; size?: 20 | 32 | 64; paused?: boolean }) {
  // The reference's "Solving" pill uses the constellation visual, named connecting upstream.
  return <ThinkingOrb className="ui-loading-orb" state={state === 'solving' ? 'connecting' : 'working'} size={size} paused={paused} theme="auto" aria-hidden="true" />;
}

export function LoadingIndicator({ children, state = 'working', size = 32 }: { children?: ReactNode; state?: LoadingState; size?: 20 | 32 | 64 }) {
  return <span className="ui-loading-indicator"><LoadingOrb state={state} size={size} />{children && <span>{children}</span>}</span>;
}

export function PageLoading({ tip = 'Loading workspace…', state = 'working', fullscreen = false }: { tip?: ReactNode; state?: LoadingState; fullscreen?: boolean }) {
  return <div className={`ui-page-loading${fullscreen ? ' ui-page-loading-fullscreen' : ''}`} role="status" aria-live="polite" aria-busy="true"><LoadingIndicator state={state} size={64}>{tip}</LoadingIndicator></div>;
}
