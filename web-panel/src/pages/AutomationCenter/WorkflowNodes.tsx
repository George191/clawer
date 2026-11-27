import { useEffect, useState } from 'react';
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
  const [selected, setSelected] = useState(0);
  const [zoom, setZoom] = useState(1);
  useEffect(() => { if (selected >= value.length) setSelected(Math.max(0, value.length - 1)); }, [selected, value.length]);
  const update = (index: number, changes: Partial<Nodes[number]>) => onChange?.(value.map((node, position) => position === index ? { ...node, ...changes } : node));
  const move = (index: number, offset: number) => {
    const next = [...value];
    [next[index], next[index + offset]] = [next[index + offset], next[index]];
    onChange?.(next); setSelected(index + offset);
  };
  const insert = (index: number) => { const next = [...value]; next.splice(index, 0, { name: '', type: 'task' }); onChange?.(next); setSelected(index); };
  return <div className="automation-node-editor windmill-flow-editor">
    <div className="windmill-flow-canvas"><div className="windmill-canvas-tools"><Button aria-label="Zoom flow in" onClick={() => setZoom(Math.min(1.5, zoom + .1))}>+</Button><Button aria-label="Zoom flow out" onClick={() => setZoom(Math.max(.5, zoom - .1))}>−</Button><Button onClick={() => setZoom(1)}>Fit</Button></div><div className="windmill-flow-graph" style={{ transform: `scale(${zoom})`, transformOrigin: 'top center' }}>
      <div className="windmill-flow-terminal">Input</div>
      {value.map((node, index) => <div className="windmill-flow-graph-step" key={index}><button type="button" className="windmill-flow-insert" aria-label={`Insert step before ${index + 1}`} onClick={() => insert(index)}>+</button><button type="button" className={`windmill-flow-node${selected === index ? ' is-selected' : ''}`} onClick={() => setSelected(index)}><span>{node.type === 'collect' ? '◉' : node.type === 'transform' ? 'ƒ' : '◇'}</span><strong>{node.name || 'Untitled step'}</strong><code>{String.fromCharCode(97 + index)}</code></button></div>)}
      <button type="button" className="windmill-flow-insert" aria-label="Insert last step" onClick={() => insert(value.length)}>+</button><div className="windmill-flow-terminal">Result</div>
    </div></div>
    <div className="automation-node-fields">{value.map((node, index) => <section key={index} className="automation-node-field" hidden={selected !== index}>
      <header><strong>Step {index + 1}</strong><span>
        <Button type="text" size="small" icon={<ArrowUpOutlined />} aria-label={`Move step ${index + 1} up`} disabled={index === 0} onClick={() => move(index, -1)} />
        <Button type="text" size="small" icon={<ArrowDownOutlined />} aria-label={`Move step ${index + 1} down`} disabled={index === value.length - 1} onClick={() => move(index, 1)} />
        <Button type="text" size="small" danger icon={<DeleteOutlined />} aria-label={`Remove step ${index + 1}`} onClick={() => (onChange?.(value.filter((_, position) => position !== index)), setSelected(Math.max(0, index - 1)))} />
      </span></header>
      <label>Step name<Input aria-label={`Step ${index + 1} name`} value={node.name} onChange={event => update(index, { name: event.target.value })} placeholder="e.g. Validate source records" /></label>
      <div className="automation-node-config"><label>Type<Select aria-label={`Step ${index + 1} type`} value={node.type || 'task'} options={['task', 'collect', 'transform', 'validate', 'publish'].map(type => ({ value: type, label: type }))} onChange={type => update(index, { type })} /></label>
        <label>Template / reference<Input aria-label={`Step ${index + 1} reference`} value={node.template || ''} onChange={event => update(index, { template: event.target.value })} placeholder="Optional reference" /></label></div>
    </section>)}<Button icon={<PlusOutlined />} onClick={() => insert(value.length)}>Add step</Button></div>
  </div>;
}
