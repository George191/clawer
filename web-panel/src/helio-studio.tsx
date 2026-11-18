import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import NativeDot, { DOT_ACTIVITIES, type DotActivity } from './components/NativeDot';
import './helio-studio.css';

const activities = Object.keys(DOT_ACTIVITIES) as DotActivity[];

function HelioStudio() {
  const [activity, setActivity] = useState<DotActivity>('Thinking');
  const [paused, setPaused] = useState(false);
  const [cycling, setCycling] = useState(false);
  const [light, setLight] = useState(false);
  useEffect(() => {
    if (!cycling || paused) return;
    const timer = window.setInterval(() => setActivity(current => activities[(activities.indexOf(current) + 1) % activities.length]), 6000);
    return () => window.clearInterval(timer);
  }, [cycling, paused]);
  return <main className={`helio-studio ${light ? 'is-light' : ''}`}>
    <header className="studio-header">
      <div className="studio-brand"><img src="/astral-helio-mark.svg" alt="" /><span>ASIRAL HELIO<span className="studio-divider">/</span>CHARACTER STUDIO</span></div>
      <button className="studio-theme" onClick={() => setLight(!light)}>{light ? '深色环境' : '浅色环境'}</button>
    </header>
    <section className="studio-title"><div><span className="studio-eyebrow">数据中台 · 统一助手</span><h1>Helio</h1></div><p>从数据采集、治理到本体仓库与情报驾驶舱，<br />同一位伙伴，连贯表达每一个工作阶段。</p></section>
    <div className="studio-workspace">
      <section className="studio-stage" aria-label="Dot 原生角色与全部活动效果">
        <div className="studio-stage-grid" />
        <div className="studio-render"><NativeDot activity={activity} paused={paused} dark={!light} /></div>
        <div className="studio-stage-footer"><span className="studio-live-dot" /><span>{paused ? '播放已暂停' : 'Dot 原生引擎'}</span><span className="studio-stage-help">原版动作 · 原版道具 · 原版光效</span></div>
      </section>
      <aside className="studio-panel">
        <span className="studio-eyebrow">完整原生活动</span><h2>{DOT_ACTIVITIES[activity].label}</h2><p>{DOT_ACTIVITIES[activity].detail}</p>
        <p className="studio-integration-note">仅调整原生外观配置；全部动作、道具与光效使用 Dot 原版。外观受原生目录限制。</p>
        <div className="studio-state-grid" role="group" aria-label="活动状态">{activities.map(key => <button key={key} aria-pressed={key === activity} onClick={() => { setActivity(key); setCycling(false); }}>{DOT_ACTIVITIES[key].label}</button>)}</div>
        <div className="studio-playback"><button aria-pressed={cycling} onClick={() => setCycling(!cycling)}>{cycling ? '停止轮播' : '自动轮播'}</button><button aria-pressed={paused} onClick={() => setPaused(!paused)}>{paused ? '继续动画' : '暂停动画'}</button></div>
        <div className="studio-small"><div className="studio-small-avatar"><NativeDot activity={activity} paused={paused} dark={!light} /></div><div><strong>小尺寸检查</strong><span>96 px · 原生效果</span></div></div>
      </aside>
    </div>
    <footer className="studio-notes"><span>原生外观配置</span><span>原版动作</span><span>原版道具</span><span>原版光效</span><a href="/dot/appearance.json" download>外观配置文件</a></footer>
  </main>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><HelioStudio /></React.StrictMode>);
