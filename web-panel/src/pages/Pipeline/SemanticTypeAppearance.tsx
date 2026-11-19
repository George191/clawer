import { useEffect, useState } from 'react';
import { Button, Input, Select } from '@/components/ui';
import { listOntologyTypeGroups, type OntologyDraft, type SemanticDefinition } from '../../services/ontology';
import './semantic-type-appearance.css';

type Props = {
  definition: SemanticDefinition;
  drafts: OntologyDraft[];
  onChange: (values: Partial<SemanticDefinition>) => void;
};

export default function SemanticTypeAppearance({ definition, drafts, onChange }: Props) {
  const [query, setQuery] = useState('');
  const [groups, setGroups] = useState<string[]>([]);
  const [loadError, setLoadError] = useState(false);
  const [inputError, setInputError] = useState('');
  const [attempt, setAttempt] = useState(0);
  const term = query.trim();
  useEffect(() => {
    const abort = new AbortController();
    setLoadError(false);
    setGroups([]);
    const timer = window.setTimeout(() => {
      listOntologyTypeGroups(term.slice(0, 120), abort.signal).then(items => {
        if (!abort.signal.aborted) setGroups(items);
      }, () => { if (!abort.signal.aborted) setLoadError(true); });
    }, term ? 180 : 0);
    return () => { window.clearTimeout(timer); abort.abort(); };
  }, [term, attempt]);
  const names = [...new Set([...definition.groups, ...groups, ...drafts.flatMap(draft => draft.definition.groups)])].sort();
  const options = names.map(name => ({ value: name, label: name }));
  if (term && term.length <= 120 && !names.includes(term)) options.unshift({ value: term, label: `Add “${term}”` });
  const changeGroups = (values: string[]) => {
    const next = [...new Set(values.map(value => value.trim()).filter(Boolean))];
    if (next.length > 20 || next.some(value => value.length > 120)) {
      setInputError('Use up to 20 groups with up to 120 characters per name.');
      return;
    }
    setInputError('');
    onChange({ groups: next });
  };

  return <>
    <div className="flow-create-field is-wide"><span>Groups</span>
      <Select className="flow-ontology__select" popupClassName="flow-ontology__select-popup" mode="tags" aria-label="Object type groups"
        placeholder="Find or add a group" value={definition.groups} options={options} onSearch={setQuery} onChange={changeGroups}
        notFoundContent={term.length > 120 ? 'Group names can have up to 120 characters.' : 'Type a name to add your first group.'} />
      <small>Catalog labels, such as Aviation. Select a group or type a new name and press Enter.</small>
      {inputError && <small role="alert">{inputError}</small>}
      {loadError && <small role="alert">Could not load published groups. <Button size="small" onClick={() => setAttempt(value => value + 1)}>Retry</Button></small>}
    </div>
    <label className="flow-create-field"><span>API ID</span>
      <Input aria-label="API ID" value={definition.id} maxLength={63}
        onChange={event => onChange({ id: event.target.value })} placeholder="airport" />
      <small>Unique within the project. Used in the Published catalog and SDK filename. Start with a lowercase letter, then use lowercase letters, digits, hyphens or underscores.</small>
    </label>
  </>;
}
