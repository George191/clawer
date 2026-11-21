import { useState } from 'react';
import { Button, Checkbox, Input, Select } from '@/components/ui';
import type { OntologyPrincipal, Principal, SemanticAction, SemanticDefinition, SemanticProperty } from '../../services/ontology';
import OntologyMarkingsEditor from './OntologyMarkingsEditor';
import OntologyRecordPolicyEditor from './OntologyRecordPolicyEditor';
import OntologyFunctionEditor from './OntologyFunctionEditor';
import OntologyIconPicker from './OntologyIconPicker';

const propertyTypes = ['string', 'integer', 'number', 'boolean', 'date', 'datetime', 'json'];
const actionKinds = ['create', 'modify', 'delete'] as const;
const principalKey = (principal: Principal) => principal.kind + ':' + principal.id;
const decodePrincipal = (key: string): Principal => {
  const separator = key.indexOf(':');
  return { kind: key.slice(0, separator) as Principal['kind'], id: key.slice(separator + 1) };
};

type Props = {
  initial: SemanticDefinition;
  definition: SemanticDefinition;
  principals: OntologyPrincipal[];
  onPrincipalSearch: (query: string) => void;
  onChange: (next: SemanticDefinition) => void;
  disabled: boolean;
};

export default function OntologyRevisionsEditor({ initial, definition, principals, onPrincipalSearch, onChange, disabled }: Props) {
  const [section, setSection] = useState('general');
  const sections = [{id: 'general', label: 'General'}, {id: 'properties', label: 'Properties', count: definition.properties.length}, ...(definition.source.mode === 'none' ? [{id: 'actions', label: 'Actions', count: definition.actions.length}, {id: 'functions', label: 'Functions', count: (definition.functions ?? []).length}] : []), {id: 'security', label: 'Security'}];
  const change = (values: Partial<SemanticDefinition>) => onChange({ ...definition, ...values });
  const projectPrincipals = principals.map(principal => ({ value: principalKey(principal), label: principal.name + ' · ' + principal.kind }));
  const primary = new Set(definition.primaryKey);
  const fieldOptions = definition.properties.filter(prop => !!prop.id).map(prop => ({ value: prop.id, label: prop.name || prop.id }));
  const security = definition.security;
  const changeProperty = (index: number, update: Partial<SemanticProperty>) => {
    change({ properties: definition.properties.map((property, position) => position === index ? { ...property, ...update } : property) });
  };
  const removeProperty = (index: number) => {
    const id = definition.properties[index].id;
    change({
      properties: definition.properties.filter((_, position) => position !== index),
      titleKey: definition.titleKey === id ? '' : definition.titleKey,
      security: { ...security, fieldReaders: Object.fromEntries(Object.entries(security.fieldReaders).filter(([key]) => key !== id)) },
      actions: definition.actions.map(action => ({ ...action, fields: action.fields.filter(field => field !== id) })),
    });
  };
  const setAction = (kind: SemanticAction['kind'], update: Partial<SemanticAction>) => {
    change({ actions: definition.actions.map(action => action.kind === kind ? { ...action, ...update } : action) });
  };
  const toggleAction = (kind: SemanticAction['kind'], checked: boolean) => {
    change({ actions: checked
      ? [...definition.actions, { kind, submitters: [], fields: kind === 'create' ? definition.properties.filter(item => item.required).map(item => item.id) : [] }]
      : definition.actions.filter(action => action.kind !== kind) });
  };
  const principalSelect = (label: string, selected: Principal[], update: (next: Principal[]) => void) => (
    <label className="flow-create-field">
      <span>{label}</span>
      <Select aria-label={label} mode="multiple" disabled={disabled} showSearch filterOption={false} onSearch={onPrincipalSearch}
        options={projectPrincipals} value={selected.map(principalKey)} onChange={(keys: string[]) => update(keys.map(decodePrincipal))} />
    </label>
  );

  return <div className="ontology-revisions__editor">
    <nav className="ontology-revisions__sections" aria-label="Proposal sections">{sections.map(item => <button type="button" key={item.id} aria-pressed={section === item.id} disabled={disabled} onClick={() => setSection(item.id)}>{item.label}{item.count != null && <span>{item.count}</span>}</button>)}</nav>
    <section className="ontology-revisions__section" aria-label={sections.find(item => item.id === section)?.label}>
    {section === 'general' && <>
      <header><h4>Object type details</h4><p>Define how this object type appears throughout the ontology.</p></header>
    <div className="flow-create-grid">
      <label className="flow-create-field"><span>Display name</span><Input maxLength={120} value={definition.name} disabled={disabled} onChange={event => change({ name: event.target.value })} /></label>
      <label className="flow-create-field"><span>Plural display name</span><Input maxLength={120} value={definition.pluralName} disabled={disabled} onChange={event => change({ pluralName: event.target.value })} /></label>
      <div className="flow-create-field"><span>Icon</span><OntologyIconPicker definition={definition} disabled={disabled} onChange={change} /></div>
    </div>
    <label className="flow-create-field"><span>Description</span><Input.TextArea maxLength={4000} rows={3} value={definition.description} disabled={disabled} onChange={event => change({ description: event.target.value })} /></label>
    <label className="flow-create-field"><span>Groups</span><Select aria-label="Groups" mode="tags" options={definition.groups.map(value => ({ value, label: value }))} value={definition.groups} disabled={disabled} onChange={(groups: string[]) => change({ groups })} /></label>

    </>}
    {section === 'properties' && <>
      <header><h4>Properties</h4><p>Manage display names, required values and the title used to identify objects.</p></header>
      <p className="flow-create-help">Existing IDs, types and source mappings are fixed. Primary key: <code>{definition.primaryKey.join(', ')}</code>.</p>
      <label className="flow-create-field"><span>Title property</span><Select aria-label="Title property" value={definition.titleKey || undefined} disabled={disabled} options={fieldOptions} onChange={(titleKey: string) => change({ titleKey })} /></label>
      {definition.properties.map((property, index) => {
        const saved = initial.properties.some(original => original.id === property.id);
        return <section className="flow-semantic-config" key={index}>
          <div className="flow-create-grid">
            <label className="flow-create-field"><span>Property ID</span><Input maxLength={63} value={property.id} disabled={disabled || saved} onChange={event => changeProperty(index, { id: event.target.value })} /></label>
            <label className="flow-create-field"><span>Property name</span><Input maxLength={120} value={property.name} disabled={disabled} onChange={event => changeProperty(index, { name: event.target.value })} /></label>
            <label className="flow-create-field"><span>Property type</span><Select aria-label="Property type" options={propertyTypes.map(value => ({ value, label: value }))} value={property.type} disabled={disabled || saved} onChange={(type: string) => changeProperty(index, { type })} /></label>
            {definition.source.mode === 'existing' && <label className="flow-create-field"><span>Source column</span><Input maxLength={63} value={property.sourceColumn ?? ''} disabled={disabled || saved} onChange={event => changeProperty(index, { sourceColumn: event.target.value })} /></label>}
          </div>
          <Checkbox checked={property.required} disabled={disabled || primary.has(property.id)} onChange={event => changeProperty(index, { required: event.target.checked })}>Required</Checkbox>
          <Button size="small" disabled={disabled || primary.has(property.id)} onClick={() => removeProperty(index)}>Remove property</Button>
        </section>;
      })}
      <Button className="ontology-revisions__add-property" size="small" disabled={disabled || definition.properties.length >= 200} onClick={() => change({ properties: [...definition.properties, { id: '', name: '', type: 'string', required: false }] })}>Add property</Button>
    </>}

    {section === 'actions' && definition.source.mode === 'none' && <>
      <header><h4>Object actions</h4><p>Choose which actions are available, who can run them and which properties they can change.</p></header>
      {actionKinds.map(kind => {
        const action = definition.actions.find(item => item.kind === kind);
        return <div className="flow-semantic-config" key={kind}>
          <Checkbox checked={!!action} disabled={disabled} onChange={event => toggleAction(kind, event.target.checked)}>{{create: 'Create', modify: 'Edit', delete: 'Delete'}[kind]} objects</Checkbox>
          {action && <>
            {principalSelect('Submitters', action.submitters, submitters => setAction(kind, { submitters }))}
            {kind !== 'delete' && <label className="flow-create-field"><span>Editable properties</span><Select aria-label="Editable properties" mode="multiple" disabled={disabled} value={action.fields} options={fieldOptions.filter(item => kind === 'create' || !primary.has(item.value))} onChange={(fields: string[]) => setAction(kind, { fields })} /></label>}
          </>}
        </div>;
      })}
    </>}

    {section === 'functions' && definition.source.mode === 'none' && <><header><h4>Business functions</h4><p>Define repeatable property updates with named input parameters. Functions use the properties enabled for Edit.</p></header><OntologyFunctionEditor embedded definition={definition} disabled={disabled} change={functions => change({functions})} /></>}
    {section === 'security' && <>
      <header><h4>Access and security</h4><p>Proposed access changes take effect only after independent review and publication.</p></header>
      <label className="flow-create-field"><span>Object security policy</span><Select aria-label="Object security policy" disabled={disabled} value={security.mode} options={[
        { value: 'explicit', label: 'Explicit readers' },
        ...(definition.source.mode === 'existing' ? [{ value: 'inherit', label: 'Inherit source access' }] : []),
      ]} onChange={(mode: 'explicit' | 'inherit') => change({ security: { ...security, mode, objectReaders: [] } })} /></label>
      {security.mode === 'explicit' && principalSelect('Object readers', security.objectReaders, objectReaders => change({ security: { ...security, objectReaders } }))}
      <label className="flow-create-field"><span>Add a property policy</span><Select aria-label="Add a property policy" disabled={disabled} options={fieldOptions.filter(item => !(item.value in security.fieldReaders))} onChange={(id: string) => change({ security: { ...security, fieldReaders: { ...security.fieldReaders, [id]: [] } } })} /></label>
      {Object.entries(security.fieldReaders).map(([id, fields]) => <div className="flow-semantic-config" key={id}>
        {principalSelect(id + ' readers', fields, readers => change({ security: { ...security, fieldReaders: { ...security.fieldReaders, [id]: readers } } }))}
        <Button size="small" disabled={disabled} onClick={() => change({ security: { ...security, fieldReaders: Object.fromEntries(Object.entries(security.fieldReaders).filter(([key]) => key !== id)) } })}>Remove property policy</Button>
      </div>)}
      <OntologyMarkingsEditor key={definition.projectId} project={definition.projectId} value={security.markings ?? []} principals={principals}
        onChange={markings => { if (!disabled) change({ security: { ...security, markings } }); }} />
      <OntologyRecordPolicyEditor definition={definition} principals={principals} onChange={next => { if (!disabled) change({ security: next }); }} />
    </>}
    </section>
  </div>;
}
