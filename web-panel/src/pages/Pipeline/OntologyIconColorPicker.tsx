import { useEffect, useId, useState, type KeyboardEvent } from 'react';
import { ontologyIconColors } from './ontologyIconColors';

const foregroundSwatches = Object.entries(ontologyIconColors).map(([key, color]) => ({
  name: key[0].toUpperCase() + key.slice(1).replace(/[A-Z]/g, letter => ' ' + letter.toLowerCase()), color,
}));
const backgroundSwatches = [...foregroundSwatches, { name: 'No background', color: '' }];

type Props = {
  mode: 'foreground' | 'background';
  value: string;
  onChange: (color: string) => void;
};

export default function OntologyIconColorPicker({ mode, value, onChange }: Props) {
  const background = mode === 'background';
  const swatches = background ? backgroundSwatches : foregroundSwatches;
  const id = useId();
  const [hex, setHex] = useState(value);
  const [invalid, setInvalid] = useState(false);
  const [active, setActive] = useState(0);
  useEffect(() => {
    setHex(value);
    setInvalid(false);
    setActive(Math.max(0, swatches.findIndex(swatch => swatch.color === value.toLowerCase())));
  }, [swatches, value]);
  const choose = (color: string) => {
    setHex(color);
    setInvalid(false);
    onChange(color);
  };
  const apply = () => {
    const raw = hex.trim();
    if (!/^#?[\da-f]{6}$/i.test(raw)) { setInvalid(true); return; }
    choose('#' + raw.replace(/^#/, '').toLowerCase());
  };
  const navigate = (event: KeyboardEvent<HTMLDivElement>) => {
    const buttons = Array.from(event.currentTarget.querySelectorAll('button'));
    const index = buttons.indexOf(event.target as HTMLButtonElement);
    const offsets: Record<string, number> = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -8, ArrowDown: 8 };
    if (index < 0 || !(event.key in offsets)) return;
    event.preventDefault();
    buttons[(index + offsets[event.key] + buttons.length) % buttons.length].focus();
  };

  return <section className="flow-color-palette" role="group" aria-label="VS Code color palette">
    <div className="flow-color-custom">
      <span className="flow-color-caption" style={{ gridColumn: 'span 2' }}>VS Code · Dark ANSI</span>
      <button type="button" onClick={() => choose(background ? '#ffffff' : '#000000')}>
        {background ? 'Default white' : 'Default black'}
      </button>
    </div>
    <div className="flow-color-swatches" onKeyDown={navigate}>
      {swatches.map(({ name, color }, index) => <button key={color} type="button" aria-label={name}
        tabIndex={active === index ? 0 : -1} onFocus={() => setActive(index)}
        className={color ? undefined : 'flow-color-none'}
        title={color ? name + ' · ' + color.toUpperCase() : name} aria-pressed={color === value.toLowerCase()}
        style={color ? { backgroundColor: color } : undefined} onClick={() => choose(color)} />)}
    </div>
    <div className="flow-color-custom">
      <label htmlFor={id + '-hex'}>Hex</label>
      <input id={id + '-hex'} aria-label="Hex color" spellCheck={false} autoComplete="off" placeholder="#RRGGBB" value={hex}
        aria-invalid={invalid || undefined} aria-describedby={invalid ? id + '-error' : undefined}
        onChange={event => { setHex(event.target.value); setInvalid(false); }}
        onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); apply(); } }} />
      <button type="button" aria-label="Apply color" onClick={apply}>Apply</button>
    </div>
    {invalid && <small id={id + '-error'} role="alert">Enter a six-digit hex color, such as #2472C8.</small>}
  </section>;
}
