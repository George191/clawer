type IconName = 'more' | 'share' | 'summary' | 'panel' | 'close' | 'send' | 'stop';

export default function ScoutToolbarIcon({ name, active = false }: { name: IconName; active?: boolean }) {
  return <svg className="scout-toolbar-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {name === 'more' && <g fill="currentColor" stroke="none"><circle cx="5" cy="12" r="1.6" /><circle cx="12" cy="12" r="1.6" /><circle cx="19" cy="12" r="1.6" /></g>}
    {name === 'share' && <><path d="M12 15V3m-4 4 4-4 4 4" /><path d="M5 12v6a3 3 0 0 0 3 3h8a3 3 0 0 0 3-3v-6" /></>}
    {name === 'summary' && <><circle cx="6" cy="5" r="2" /><circle cx="6" cy="12" r="2" /><circle cx="6" cy="19" r="2" /><path d="M12 5h8m-8 7h8m-8 7h8" /></>}
    {name === 'panel' && <>{active && <path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3Z" fill="currentColor" fillOpacity=".25" stroke="none" />}<rect x="4" y="4" width="16" height="16" rx="3" /><path d="M15 4v16" /></>}
    {name === 'close' && <path d="m7 7 10 10M17 7 7 17" />}
    {name === 'send' && <path d="M12 19V5m-5 5 5-5 5 5" strokeWidth="2" />}
    {name === 'stop' && <rect x="6.5" y="6.5" width="11" height="11" rx="1.5" fill="currentColor" stroke="none" />}
  </svg>;
}
