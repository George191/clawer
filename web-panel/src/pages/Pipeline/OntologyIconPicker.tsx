import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react';
import { Button, Modal } from '@/components/ui';
import { CheckOutlined, DownOutlined, RightOutlined, SearchOutlined } from '@/components/ui/icons';
import type { SemanticDefinition } from '../../services/ontology';
import OntologyIconColorPicker from './OntologyIconColorPicker';
import { ontologyIconChoices } from './ontologyTypeIcons';
import SemanticTypeGlyph from './SemanticTypeGlyph';
import './semantic-icon-library.css';

const choices = [{ value: '', label: 'Automatic' }, ...ontologyIconChoices];
const pageSize = 24;
const tabs = ['Icons', 'Icon color', 'Background'] as const;

type Props = {
  definition: SemanticDefinition;
  disabled?: boolean;
  onChange: (fields: Pick<Partial<SemanticDefinition>, 'icon' | 'color' | 'iconColor'>) => void;
};

export default function OntologyIconPicker({ definition, disabled, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<typeof tabs[number]>('Icons');
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(0);
  const [focusedIcon, setFocusedIcon] = useState(definition.icon);
  const search = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const id = useId();
  const term = query.trim().toLowerCase();
  const matches = choices.filter(({ label, value }) => (label + ' ' + value).toLowerCase().includes(term));
  const pageCount = Math.ceil(matches.length / pageSize);
  const visible = matches.slice(page * pageSize, (page + 1) * pageSize);
  const focusIndex = Math.max(0, visible.findIndex(({ value }) => value === focusedIcon));
  const selected = choices.find(({ value }) => value === definition.icon)?.label || definition.icon;

  useEffect(() => { if (open) search.current?.focus(); }, [open]);
  const show = () => {
    const selectedIndex = choices.findIndex(({ value }) => value === definition.icon);
    setPage(Math.floor(Math.max(0, selectedIndex) / pageSize));
    setQuery(''); setTab('Icons'); setFocusedIcon(definition.icon); setOpen(true);
  };
  const navigate = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!list.current || !(event.target instanceof HTMLButtonElement)) return;
    const buttons = Array.from(list.current.querySelectorAll('button'));
    const index = buttons.indexOf(event.target);
    const columns = getComputedStyle(list.current).gridTemplateColumns.split(' ').length;
    const positions: Record<string, number> = {
      ArrowLeft: index - 1, ArrowRight: index + 1, ArrowUp: index - columns,
      ArrowDown: index + columns, Home: 0, End: buttons.length - 1,
    };
    if (!Object.prototype.hasOwnProperty.call(positions, event.key)) return;
    event.preventDefault();
    buttons[Math.max(0, Math.min(buttons.length - 1, positions[event.key]))]?.focus();
  };

  return <>
    <button type="button" className="flow-icon-trigger" aria-label="Object type icon" aria-haspopup="dialog"
      aria-expanded={open} title={selected} onClick={show} disabled={disabled}>
      <SemanticTypeGlyph definition={definition} /><DownOutlined />
    </button>
    {open && <Modal open centered title="Icon appearance" width={360} zIndex={1100}
      className="flow-surface-modal flow-create-modal flow-appearance-dialog" onCancel={() => setOpen(false)}
      footer={<Button type="primary" onClick={() => setOpen(false)}>Done</Button>}>
      <div className="flow-appearance-preview">
        <SemanticTypeGlyph definition={definition} size="preview" />
        <small>Icon {definition.iconColor ?? '#000000'} · {definition.color === '' ? 'No background' : 'Background ' + (definition.color ?? '#ffffff')}</small>
      </div>
      <div className="flow-appearance-tabs" role="tablist" aria-label="Icon appearance settings">
        {tabs.map((name, index) => <button type="button" role="tab" key={name} id={id + '-tab-' + index}
          tabIndex={name === tab ? 0 : -1} aria-selected={name === tab} aria-controls={id + '-panel'}
          onClick={() => setTab(name)} onKeyDown={event => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
            event.preventDefault();
            const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1
              : (index + (event.key === 'ArrowLeft' ? -1 : 1) + tabs.length) % tabs.length;
            setTab(tabs[next]); document.getElementById(id + '-tab-' + next)?.focus();
          }}>{name}</button>)}
      </div>
      <div id={id + '-panel'} role="tabpanel" aria-labelledby={id + '-tab-' + tabs.indexOf(tab)} className="flow-appearance-panel">
        {tab === 'Icons' ? <>
          <label className="flow-icon-search"><SearchOutlined />
            <input ref={search} type="search" aria-label="Search icons" placeholder="Search icons" value={query}
              onChange={event => { setQuery(event.target.value); setPage(0); }}
              onKeyDown={event => {
                if (event.key === 'ArrowDown' && visible.length) {
                  event.preventDefault(); list.current?.querySelector<HTMLButtonElement>('button[tabindex="0"]')?.focus();
                }
              }} />
          </label>
          <div ref={list} className="flow-icon-options" role="listbox" aria-label="Object type icons" onKeyDown={navigate}>
            {visible.map(({ value, label }, index) => <button type="button" key={value} role="option" aria-label={label} title={label}
              aria-selected={value === definition.icon} tabIndex={index === focusIndex ? 0 : -1} onFocus={() => setFocusedIcon(value)}
              onClick={() => onChange({ icon: value })}>
              <SemanticTypeGlyph definition={{ ...definition, icon: value, color: '#ffffff', iconColor: '#000000' }} />
              {value === definition.icon && <CheckOutlined className="flow-icon-check" />}
            </button>)}
          </div>
          {matches.length ? <nav className="flow-icon-pagination" aria-label="Icon pages">
            <span role="status">Page {page + 1} of {pageCount} · {matches.length} {matches.length === 1 ? 'choice' : 'choices'}</span>
            <Button size="small" icon={<RightOutlined rotate={180} />} aria-label="Previous icon page" disabled={page === 0} onClick={() => setPage(page - 1)} />
            <Button size="small" icon={<RightOutlined />} aria-label="Next icon page" disabled={page + 1 >= pageCount} onClick={() => setPage(page + 1)} />
          </nav> : <p role="status">No matching icons.</p>}
        </> : tab === 'Background'
          ? <OntologyIconColorPicker key={tab} mode="background" value={definition.color ?? '#ffffff'} onChange={color => onChange({ color })} />
          : <OntologyIconColorPicker key={tab} mode="foreground" value={definition.iconColor ?? '#000000'} onChange={iconColor => onChange({ iconColor })} />}
      </div>
    </Modal>}
  </>;
}
