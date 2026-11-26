// Original Devicon assets and license are shipped in public/file-icons.
const pythonIcon = '/file-icons/python.svg';
const yamlIcon = '/file-icons/yaml.svg';

export type StageArtifact = { key: string; turn: number; step: number };

export function ArtifactIcon({ step }: { step: number }) {
  if (step === 2 || step === 3) return <img className="scout-file-icon" src={step === 2 ? yamlIcon : pythonIcon} alt="" aria-hidden="true" />;
  return <svg viewBox="0 0 24 24" aria-hidden="true" style={{ color: step === 1 ? '#76baff' : step === 5 ? '#a4cf83' : step === 4 ? '#c4a5ed' : '#e6bc78' }}>{step === 1 ? <><circle cx="12" cy="12" r="8" /><ellipse cx="12" cy="12" rx="3.5" ry="8" /><path d="M4 12h16" /></> : <><path d="M6 3h8l4 4v14H6V3Zm8 0v5h4" />{step === 5 ? <path d="M9 11h6m-6 3h6m-6 3h6M12 11v6" /> : <path d="M9 12h6m-6 4h4" />}</>}</svg>;
}
