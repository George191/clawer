import React from 'react';
import { Button, Input, Select } from '@/components/ui';
import type { OntologyPrincipal, Principal, RecordRule, SemanticDefinition, SemanticSecurity } from '../../services/ontology';

type Props = {
  definition: SemanticDefinition;
  principals: OntologyPrincipal[];
  onChange: (security: SemanticSecurity) => void;
};

const principalKey = (principal: Principal) => principal.kind + ':' + principal.id;
const allowedTypes = ['string', 'integer', 'number', 'boolean'];
const initialValue = (type?: string) => type === 'boolean' ? false : type === 'integer' || type === 'number' ? 0 : '';

function NumericRuleValue({ value, onChange }: { value: unknown; onChange: (value: string | number) => void }) {
  const [editing, setEditing] = React.useState<string>();
  return <Input inputMode="decimal" value={editing ?? String(value ?? '')} onBlur={() => setEditing(undefined)}
    onChange={e => {
      const raw = e.target.value;
      setEditing(raw);
      const number = Number(raw);
      onChange(raw.trim() && Number.isFinite(number) && (!Number.isInteger(number) || Number.isSafeInteger(number)) ? number : raw);
    }} />;
}

export default function OntologyRecordPolicyEditor({ definition, principals, onChange }: Props) {
  const rules = definition.security.recordRules ?? [];
  const fields = definition.properties.filter(p => allowedTypes.includes(p.type));
  const change = (recordRules: RecordRule[]) => onChange({ ...definition.security, recordRules });
  const update = (index: number, rule: RecordRule) => change(rules.map((item, i) => i === index ? rule : item));
  const readers = new Map(principals.map(principal => [principalKey(principal), principal]));
  for (const rule of rules) for (const p of rule.readers) {
    if (!readers.has(principalKey(p))) readers.set(principalKey(p), { ...p, name: p.id });
  }

  return <section className="flow-semantic-config" aria-label="Record access rules">
    <h4>Record access</h4>
    <p className="flow-create-help">{rules.length
      ? 'A reader must match at least one rule as well as the object policy. Actions check the record before and after an edit.'
      : 'The object policy currently applies to every record.'}</p>
    {rules.map((rule, i) => {
      const type = rule.kind === 'property' ? fields.find(p => p.id === rule.field)?.type : undefined;
      return <fieldset key={i} className="flow-semantic-config"><legend>{rule.kind === 'property' ? 'Property rule' : 'Relationship rule'} {i + 1}</legend>
        <label className="flow-create-field"><span>Rule readers</span>
          <Select mode="multiple" aria-label={'Readers for rule ' + (i + 1)} value={rule.readers.map(principalKey)}
            options={[...readers].map(([value, p]) => ({ value, label: p.name + ' (' + p.kind + ')' }))}
            onChange={(keys: string[]) => update(i, { ...rule, readers: keys.map(key => {
              const { kind, id } = readers.get(key)!; return { kind, id };
            }) })} /></label>
        {rule.kind === 'property' ? <>
          <label className="flow-create-field"><span>Record property</span><select value={rule.field}
            onChange={e => update(i, { ...rule, field: e.target.value, match: 'equals', value: initialValue(fields.find(p => p.id === e.target.value)?.type) })}>
            {fields.map(p => <option key={p.id} value={p.id}>{p.name || p.id}</option>)}</select></label>
          <label className="flow-create-field"><span>Compare with</span><select value={rule.match}
            onChange={e => update(i, e.target.value === 'currentUser' ? { ...rule, match: 'currentUser', value: null } : { ...rule, match: 'equals', value: initialValue(type) })}>
            <option value="equals">A fixed value</option>{type === 'string' && <option value="currentUser">Signed-in user ID</option>}</select></label>
          {rule.match === 'equals' && <label className="flow-create-field"><span>Required value</span>{type === 'boolean'
            ? <select value={String(rule.value)} onChange={e => update(i, { ...rule, value: e.target.value === 'true' })}><option>true</option><option>false</option></select>
            : type === 'integer' || type === 'number'
              ? <NumericRuleValue value={rule.value} onChange={value => update(i, { ...rule, value })} />
              : <Input value={String(rule.value ?? '')} onChange={e => update(i, { ...rule, value: e.target.value })} />}</label>}
        </> : <>
          <label className="flow-create-field"><span>Link type key</span><Input value={rule.linkType} onChange={e => update(i, { ...rule, linkType: e.target.value })} /></label>
          <label className="flow-create-field"><span>Link direction from this record</span><select value={rule.direction} onChange={e => update(i, { ...rule, direction: e.target.value as 'incoming' | 'outgoing' })}>
            <option value="outgoing">Outgoing</option><option value="incoming">Incoming</option></select></label>
          <label className="flow-create-field"><span>Related record ID</span><Input value={rule.relatedRecordId} onChange={e => update(i, { ...rule, relatedRecordId: e.target.value })} /></label>
        </>}
        <Button size="small" onClick={() => change(rules.filter((_, index) => index !== i))}>Remove rule</Button>
      </fieldset>;
    })}
    <div className="flow-create-grid">
      <Button disabled={!fields.length || rules.length >= 32} onClick={() => change([...rules, { kind: 'property', field: fields[0].id, match: 'equals', value: initialValue(fields[0].type), readers: [] }])}>Add property rule</Button>
      {definition.source.mode === 'none' && <Button disabled={rules.length >= 32} onClick={() => change([...rules, { kind: 'related', linkType: '', direction: 'outgoing', relatedRecordId: '', readers: [] }])}>Add relationship rule</Button>}
    </div>
  </section>;
}
