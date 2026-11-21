import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const jsx = (type, props) => ({ type, props });
const nodes = tree => tree && typeof tree === 'object' ? [tree, ...[tree.props?.children].flat(Infinity).flatMap(nodes)] : [];
const text = tree => typeof tree === 'string' || typeof tree === 'number' ? String(tree) : tree && typeof tree === 'object' ? [tree.props?.children].flat(Infinity).map(text).join('') : '';
function compile(name, require) {
  const source = readFileSync(new URL('../src/pages/Pipeline/' + name, import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const exports = {};
  vm.runInThisContext('(function(require,exports){' + code + '\n})')(require, exports);
  return exports;
}
const values = compile('ontologyPropertyValue.ts', () => ({}));
const objectType = { id: 'qa-type', revision: 2, definition: { id: 'qa-object', name: 'QA object', source: { mode: 'none' }, properties: [
  { id: 'id', name: 'Id', type: 'string', required: true }, { id: 'metadata', name: 'Metadata', type: 'json', required: true },
  { id: 'count', name: 'Count', type: 'number', required: false },
] } };
const createAction = { kind: 'create', fields: ['id', 'metadata', 'count'] };

function mountTools(patch = {}) {
  const states = [], refs = [], calls = [], notifications = [];
  let cursor = 0, result, changed = 0;
  const input = Object.assign(() => null, { TextArea: 'TextArea' });
  const module = compile('PublishedObjectTools.tsx', name => {
    if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx };
    if (name === 'react') return {
      useState(initial) { const index = cursor++; if (!(index in states)) states[index] = initial; return [states[index], next => { states[index] = typeof next === 'function' ? next(states[index]) : next; }]; },
      useRef(initial) { const index = cursor++; return refs[index] ||= { current: initial }; }, useEffect() {},
    };
    if (name === '@/components/ui') return { Button: 'Button', Checkbox: 'Checkbox', Input: input, Modal: 'Modal', message: {
      success: value => notifications.push(value), error: value => notifications.push(value),
    } };
    if (name === '@/components/ui/icons') return new Proxy({}, { get: (_, key) => String(key) });
    if (name === '../../services/session') return { getSessionState: () => ({ identity: 'qa', authenticated: true, locked: false }) };
    if (name === '../../services/ontology') return { ontologyError: (error, fallback) => error.message || fallback,
      submitOntologyAction: async (...args) => { calls.push(args); },
    };
    if (name === './ontologyPropertyValue') return values;
    if (name === './useExplorerErrorToast') return { default() {} };
    return { default: name };
  });
  const render = () => { cursor = 0; result = module.usePublishedObjectTools({ objectType, actions: [createAction], onChanged: () => changed++, ...patch }); return result; };
  render();
  return { render, result: () => result, calls, notifications, changed: () => changed,
    dialog: () => nodes(result.dialogs).find(node => node.type === 'Modal' && node.props.open && node.props.title === 'Create object'),
  };
}

test('creation remains restricted to permitted native object types and respects busy state', () => {
  assert.ok(mountTools().result().createButton);
  assert.equal(mountTools({ actions: [] }).result().createButton, null);
  assert.equal(mountTools({ objectType: { ...objectType, definition: { ...objectType.definition, source: { mode: 'existing' } } } }).result().createButton, null);
  const busy = mountTools({ busy: true });
  assert.equal(busy.result().createButton.props.disabled, true);
  busy.result().createButton.props.onClick(); busy.render();
  assert.equal(busy.dialog(), undefined);
});

test('the create form preserves required validation, parses JSON and sends explicit nulls', async () => {
  const ui = mountTools();
  ui.result().createButton.props.onClick(); ui.render();
  assert.ok(ui.dialog().props.className.includes('published-create-object-dialog'));
  assert.equal(ui.dialog().props.okText, 'Create object');
  const field = label => nodes(ui.dialog()).find(node => node.props?.['aria-label'] === label);
  assert.equal(field('Metadata').type, 'TextArea');
  assert.equal(field('Id').props['aria-required'], true);
  await ui.dialog().props.onOk(); ui.render();
  assert.ok(nodes(ui.dialog()).some(node => node.props?.role === 'alert' && text(node).includes('Id is required')));
  assert.equal(ui.calls.length, 0);
  field('Id').props.onChange({ target: { value: 'A-17' } });
  field('Metadata').props.onChange({ target: { value: '{bad' } }); ui.render();
  await ui.dialog().props.onOk(); ui.render();
  assert.ok(nodes(ui.dialog()).some(node => node.props?.role === 'alert' && text(node).includes('valid JSON')));
  assert.equal(ui.calls.length, 0);
  field('Metadata').props.onChange({ target: { value: '{"tags":["photo","pdf"]}' } });
  field('Set Count to null').props.onChange({ target: { checked: true } }); ui.render();
  await ui.dialog().props.onOk(); ui.render();
  assert.deepEqual(ui.calls, [[objectType.id, 'create', { values: { id: 'A-17', metadata: { tags: ['photo', 'pdf'] }, count: null } }]]);
  assert.equal(ui.dialog(), undefined);
  assert.equal(ui.changed(), 1);
});

test('the create control follows Open in without occupying the left toolbar', () => {
  const module = compile('ExplorerWorkbenchHeader.tsx', name => {
    if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx };
    if (name === 'react') return { useState: initial => [initial, () => {}], useRef: current => ({ current }), useEffect() {}, useMemo: read => read(), useId: () => 'test' };
    if (name === '@/components/ui') return { Button: 'Button', Dropdown: 'Dropdown', Modal: 'Modal' };
    if (name === '@/components/ui/icons') return new Proxy({}, { get: (_, key) => String(key) });
    if (name === '../../services/explorerSaved') return { publishedSavedPath: () => ({ path: '/saved' }) };
    if (name === './ExplorerExploreView') return { chooseExploreField: () => '' };
    if (name === './useExplorerAggregation') return { default: () => ({}) };
    return { default: name };
  });
  const control = jsx('Button', { children: 'Create object' });
  const tree = module.default({ source: { kind: 'published', definitionId: objectType.id }, label: 'QA object', available: true,
    fields: [], rows: [], selected: [], query: {}, resource: 'preview', availableResources: ['preview'], mode: 'results', createControl: control,
  });
  const right = nodes(tree).find(node => node.props?.className === 'wb-right');
  assert.equal(right.props.children[1], control);
  assert.ok(text(right.props.children[0]).includes('Open in'));
  assert.ok(!nodes(nodes(tree).find(node => node.props?.className === 'wb-left')).includes(control));
});

test('modify and delete keep their original dialog content and submission labels', () => {
  const record = { id: 'record', revision: 1, properties: { metadata: { tags: ['photo'] } } };
  for (const kind of ['modify', 'delete']) {
    const ui = mountTools({ actions: [{ kind, fields: kind === 'modify' ? ['metadata'] : [] }] });
    ui.result().actionsForRecord(record)[0].onClick(); ui.render();
    const dialog = nodes(ui.result().dialogs).find(node => node.type === 'Modal' && node.props.open);
    assert.equal(dialog.props.className, 'flow-surface-modal flow-published-dialog');
    assert.equal(dialog.props.width, 520);
    assert.equal(dialog.props.okText, kind === 'modify' ? 'Submit action' : 'Delete object');
    assert.ok(!nodes(dialog).some(node => node.type === 'TextArea' || node.props?.className === 'published-create-object-context'));
  }
});
