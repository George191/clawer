import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

export const artifactLabels = ['Request brief', 'Webpage', 'template.yaml', 'adapter.py', 'Collection plan', 'Sample data'];
export type StageArtifact = { key: string; turn: number; step: number };
type Flight = { artifact: StageArtifact; x: number; y: number; dx: number; dy: number; delay: number };

export default function StageArtifacts({ artifacts, selected, onView }: { artifacts: StageArtifact[]; selected?: string; onView: (artifact: StageArtifact) => void }) {
  const tray = useRef<HTMLElement>(null);
  const seen = useRef(new Set<string>());
  const [landed, setLanded] = useState(new Set<string>());
  const [flights, setFlights] = useState<Flight[]>([]);
  const signature = artifacts.map((item) => item.key).join(',');
  useEffect(() => {
    const added = artifacts.filter((item) => !seen.current.has(item.key));
    if (!added.length) return;
    added.forEach((item) => seen.current.add(item.key));
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      setLanded((prev) => new Set([...prev, ...added.map((item) => item.key)]));
      return;
    }
    const origin = document.querySelector('.scout-dot-host')!.getBoundingClientRect();
    const pending = added.map((artifact, index) => {
      const target = tray.current!.querySelector<HTMLElement>(`[data-artifact="${artifact.key}"]`)!;
      tray.current!.scrollLeft = Math.max(0, target.offsetLeft + target.offsetWidth - tray.current!.clientWidth);
      const end = target.getBoundingClientRect();
      const x = origin.left + origin.width / 2;
      const y = origin.top + origin.height / 2;
      return { artifact, x, y, dx: end.left + end.width / 2 - x, dy: end.top + end.height / 2 - y, delay: index * 140 };
    });
    setFlights((prev) => [...prev, ...pending]);
  }, [signature]);
  function arrive(key: string) {
    setLanded((prev) => new Set([...prev, key]));
    setFlights((prev) => prev.filter((flight) => flight.artifact.key !== key));
  }
  return <>
    <nav ref={tray} className="scout-artifact-tray" aria-label="Stage results">{artifacts.map((item) => <button key={item.key} type="button" className={`scout-artifact ${landed.has(item.key) ? 'is-landed' : 'is-arriving'}`} data-artifact={item.key} aria-label={`View ${artifactLabels[item.step]}`} title={artifactLabels[item.step]} aria-pressed={selected === item.key} aria-busy={!landed.has(item.key)} disabled={!landed.has(item.key)} onClick={() => onView(item)}><ArtifactIcon step={item.step} /><span className="scout-artifact-check" aria-hidden="true">✓</span></button>)}</nav>
    {createPortal(flights.map((flight) => <span key={flight.artifact.key} className="scout-artifact-flight" aria-hidden="true" style={{ left: flight.x - 18, top: flight.y - 18, '--fly-dx': `${flight.dx}px`, '--fly-dy': `${flight.dy}px`, animationDelay: `${flight.delay}ms` } as React.CSSProperties} onAnimationEnd={() => arrive(flight.artifact.key)}><ArtifactIcon step={flight.artifact.step} /></span>), document.body)}
  </>;
}

function ArtifactIcon({ step }: { step: number }) {
  return <svg viewBox="0 0 24 24" aria-hidden="true">{step === 1 ? <><circle cx="12" cy="12" r="8" /><ellipse cx="12" cy="12" rx="3.5" ry="8" /><path d="M4 12h16" /></> : <><path d="M6 3h8l4 4v14H6V3Zm8 0v5h4" />{step === 2 ? <path d="m10 11-2 3 2 3m4-6 2 3-2 3" /> : step === 3 ? <path d="m10 11-2 3 2 3m4-6 2 3-2 3m-2-6-2 6" /> : step === 5 ? <path d="M9 11h6m-6 3h6m-6 3h6M12 11v6" /> : <path d="M9 12h6m-6 4h4" />}</>}</svg>;
}
