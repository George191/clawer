import { Button, NativeSelect } from '@/components/ui';
import type { BusinessFunction, FunctionAssignment, FunctionOperand, SemanticDefinition } from '../../services/ontology';

export default function OntologyFunctionEditor({definition, disabled, change, embedded = false}: {
  definition: SemanticDefinition; disabled: boolean; embedded?: boolean; change: (next: BusinessFunction[]) => void;
}) {
  const functions = definition.functions ?? [];
  const fields = definition.properties.filter(p => definition.actions.some(a => a.kind === 'modify' && a.fields.includes(p.id)));
  const first = (): FunctionAssignment => ({field: fields[0]?.id ?? '', operation: 'set', operands: [{parameter: 'value'}]});
  const update = (index: number, patch: Partial<BusinessFunction>) =>
    change(functions.map((fn, i) => i === index ? {...fn, ...patch} : fn));
  const content = <fieldset disabled={disabled}>
    {functions.map((fn, i) => <fieldset key={i}><legend>{fn.name || 'New function'}</legend>
      <label>Function identifier<input value={fn.key} maxLength={63} onChange={e => update(i,{key:e.target.value})} /></label>
      <label>Function name<input value={fn.name} maxLength={100} onChange={e => update(i,{name:e.target.value})} /></label>
      {fn.assignments.map((a,j) => {
        const field = fields.find(p => p.id === a.field);
        const type = field?.type;
        const set = (patch: Partial<FunctionAssignment>) => update(i,{assignments:fn.assignments.map((item,k) => j === k ? {...a,...patch} : item)});
        const operand = (index: number, next: FunctionOperand) => set({operands:a.operands.map((item,k) => k === index ? next : item)});
        return <fieldset key={j}><legend>Output {j+1}</legend>
          <label>Output property<NativeSelect aria-label="Output property" value={a.field} onChange={e => set({field:e.target.value,operation:'set',operands:[{parameter:'value'}]})}>
            {fields.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</NativeSelect></label>
          <label>Calculation<NativeSelect aria-label="Calculation" value={a.operation} onChange={e => {
            const op = e.target.value as FunctionAssignment['operation'];
            set({operation:op,operands:op === 'set' ? [a.operands[0]] : [a.operands[0],a.operands[1] ?? {parameter:'second'}]});
          }}><option value="set">Assign</option>
            {(type === 'integer' || type === 'number') && <><option value="add">Add</option><option value="multiply">Multiply</option></>}
            {type === 'string' && <option value="concat">Concatenate</option>}</NativeSelect></label>
          {a.operands.map((term,k) => <div key={k}><label>Operand {k+1}<NativeSelect aria-label={'Operand '+(k+1)} value={'parameter' in term ? 'parameter' : 'constant'}
            onChange={e => operand(k,e.target.value === 'parameter' ? {parameter:'value'} : {value:type === 'boolean' ? false : type === 'number' || type === 'integer' ? 0 : ''})}>
            <option value="parameter">Parameter</option>{type !== 'json' && <option value="constant">Constant</option>}</NativeSelect></label>
            {'parameter' in term ? <label>Parameter {k+1} name<input value={term.parameter} onChange={e => operand(k,{parameter:e.target.value})} /></label>
              : type === 'boolean' ? <label>Constant {k+1}<NativeSelect aria-label={'Constant '+(k+1)} value={String(term.value)} onChange={e => operand(k,{value:e.target.value === 'true'})}><option>true</option><option>false</option></NativeSelect></label>
              : <label>Constant {k+1}<input value={String(term.value ?? '')} onChange={e => {
                const v = e.target.value;
                operand(k,{value:(type === 'integer' || type === 'number') && v.trim() && Number.isFinite(Number(v)) ? Number(v) : v});
              }} /></label>}</div>)}
          <Button size="small" disabled={fn.assignments.length === 1} onClick={() => update(i,{assignments:fn.assignments.filter((_,k) => j !== k)})}>Remove output</Button>
        </fieldset>;
      })}
      <Button size="small" disabled={!fields.length || fn.assignments.length >= 24} onClick={() => update(i,{assignments:[...fn.assignments,first()]})}>Add output</Button>
      <Button size="small" onClick={() => change(functions.filter((_,j) => j !== i))}>Remove function</Button>
    </fieldset>)}
    <Button disabled={!fields.length || functions.length >= 24} onClick={() => change([...functions,{key:'',name:'',assignments:[first()]}])}>Add function</Button>
    {!fields.length && <p>Enable Edit and select an editable property to define a function.</p>}
  </fieldset>;
  return embedded ? content : <details><summary>Business functions</summary>{content}</details>;
}
