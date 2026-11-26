import { Button, Input, Select } from '@/components/ui';
import { ArrowDownOutlined, ArrowUpOutlined, DeleteOutlined, PlusOutlined } from '@/components/ui/icons';
import type { AutomationWorkflow } from '@/services/types';

type Nodes = AutomationWorkflow['nodes'];

export function WorkflowPath({ nodes }: { nodes: Nodes }) {
  return <ol className="automation-workflow-path" aria-label="Workflow execution sequence">
    {nodes.map((node, index) => <li key={index}><span className="automation-step-number">{index + 1}</span><div><strong>{node.name || 'Untitled step'}</strong><small>{node.type || 'Step'}{node.template ? ` · ${node.template}` : ''}</small></div></li>)}
    {!nodes.length && <li className="automation-muted">Add a step to define the execution sequence.</li>}
  </ol>;
}

export default function WorkflowNodes({ value = [], onChange }: { value?: Nodes; onChange?: (nodes: Nodes) => void }) {
  const update = (index: number, changes: Partial<Nodes[number]>) => onChange?.(value.map((node, position) => position === index ? { ...node, ...changes } : node));
  const move = (index: number, offset: number) => {
    const next = [...value];
    [next[index], next[index + offset]] = [next[index + offset], next[index]];
    onChange?.(next);
  };
  return <div className="automation-node-editor">
    <div className="automation-node-fields">{value.map((node, index) => <section key={index} className="automation-node-field">
      <header><strong>Step {index + 1}</strong><span>
        <Button type="text" size="small" icon={<ArrowUpOutlined />} aria-label={`Move step ${index + 1} up`} disabled={index === 0} onClick={() => move(index, -1)} />
        <Button type="text" size="small" icon={<ArrowDownOutlined />} aria-label={`Move step ${index + 1} down`} disabled={index === value.length - 1} onClick={() => move(index, 1)} />
        <Button type="text" size="small" danger icon={<DeleteOutlined />} aria-label={`Remove step ${index + 1}`} onClick={() => onChange?.(value.filter((_, position) => position !== index))} />
      </span></header>
      <label>Step name<Input aria-label={`Step ${index + 1} name`} value={node.name} onChange={event => update(index, { name: event.target.value })} placeholder="e.g. Validate source records" /></label>
      <div className="automation-node-config"><label>Type<Select aria-label={`Step ${index + 1} type`} value={node.type || 'task'} options={['task', 'collect', 'transform', 'validate', 'publish'].map(type => ({ value: type, label: type }))} onChange={type => update(index, { type })} /></label>
        <label>Template / reference<Input aria-label={`Step ${index + 1} reference`} value={node.template || ''} onChange={event => update(index, { template: event.target.value })} placeholder="Optional reference" /></label></div>
    </section>)}<Button icon={<PlusOutlined />} onClick={() => onChange?.([...value, { name: '', type: 'task' }])}>Add step</Button></div>
    <aside className="automation-node-preview"><span className="automation-eyebrow">SEQUENCE PREVIEW</span><WorkflowPath nodes={value} /></aside>
  </div>;
}
