import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const jsx = (type, props) => ({ type, props });
const nodes = tree => tree && typeof tree === 'object' ? [tree, ...[tree.props?.children].flat(Infinity).flatMap(nodes)] : [];
const text = tree => typeof tree === 'string' || typeof tree === 'number' ? String(tree) : tree && typeof tree === 'object' ? [tree.props?.children].flat(Infinity).map(text).join('') : '';
const button = (tree, label) => nodes(tree).find(node => ['Button', 'button'].includes(node.type) && text(node) === label);
const input = (tree, label) => nodes(tree).find(node => node.props?.['aria-label'] === label);

function mount(file, props, api, exported = 'default') {
  const states = [], refs = [], effects = [], scheduled = [], notifications = [], errors = [];
  const session = { identity: 'qa-account', authenticated: true, locked: false };
  let cursor = 0, tree, requestId = 0;
  const parser = ts.transpileModule(readFileSync(new URL('../src/pages/Pipeline/ontologyPropertyValue.ts', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  const values = {};
  vm.runInThisContext('(function(exports){' + parser + '\n})')(values);
  const module = {};
  const source = ts.transpileModule(readFileSync(new URL('../src/pages/Pipeline/' + file + '.tsx', import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const require = name => {
    if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx, Fragment: 'Fragment' };
    if (name === 'react') return {
      useState(initial) { const i = cursor++; if (!(i in states)) states[i] = typeof initial === 'function' ? initial() : initial;
        return [states[i], next => { states[i] = typeof next === 'function' ? next(states[i]) : next; }]; },
      useRef(initial) { return refs[cursor++] ||= { current: initial }; },
      useEffect(effect, deps) { const i = cursor++; if (!effects[i] || !deps || deps.some((value, j) => !Object.is(value, effects[i].deps[j]))) scheduled.push([i, effect, deps]); },
    };
    if (name === '@/components/ui') return { Button: 'Button', Input: Object.assign(() => null, { TextArea: 'TextArea' }), Checkbox: 'Checkbox', Select: 'Select',
      message: Object.fromEntries(['success', 'error'].map(kind => [kind, value => notifications.push({ kind, value })])) };
    if (name === '@/components/ui/icons') return new Proxy({}, { get: (_, key) => String(key) });
    if (name === '../../services/session') return { getSessionState: () => session };
    if (name === '../../services/ontology') return { ...api, newOntologyRequestId: () => 'qa-request-' + ++requestId,
      ontologyError: (error, fallback) => error.message || fallback };
    if (name === '../../services/ontology_graph' || name === '../../services/ontology_revisions') return api;
    if (name === './ontologyPropertyValue') return values;
    if (name === './useExplorerErrorToast') return { default: (...args) => errors.push(args) };
    return { default: name };
  };
  vm.runInThisContext('(function(require,exports,window){' + source + '\n})')(require, module, { setTimeout, clearTimeout });
  const render = () => {
    cursor = 0; scheduled.length = 0; tree = module[exported](props);
    for (const [i, effect, deps] of scheduled) { effects[i]?.cleanup?.(); effects[i] = { deps, cleanup: effect() }; }
    return tree;
  };
  const flush = async () => { for (let i = 0; i < 6; i++) { await Promise.resolve(); render(); } return tree; };
  render();
  return { render, flush, session, notifications, errors, tree: () => tree, props,
    unmount: () => effects.forEach(effect => effect?.cleanup?.()) };
}

const record = { id: 'qa-record', title: 'Synthetic target', revision: 4, properties: {} };
const catalog = { revision: 9, items: [{ key: 'update', name: 'Update synthetic target', parameters: [
  { key: 'amount', type: 'number', nullable: false }, { key: 'payload', type: 'json', nullable: false },
  { key: 'flag', type: 'boolean', nullable: true },
] }] };
function functions(post = async () => ({ name: 'Synthetic update', stepCount: 3 }), entries = catalog) {
  const calls = [], committed = [];
  const ui = mount('OntologyFunctions', { id: 'qa-type', record, onCommitted: name => committed.push(name) }, {
    graphRequest: async (...args) => { calls.push(args); return args[0] === 'get' ? entries : post(...args); },
  });
  return { ...ui, calls, committed, posts: () => calls.filter(call => call[0] === 'post') };
}
function fillArguments(ui) {
  for (const [key, value] of [['amount', '2.5'], ['payload', '{"tags":["synthetic"]}'], ['flag', 'false']]) {
    input(ui.tree(), 'Argument ' + key).props.onChange({ target: { value } });
  }
  ui.render();
}

test('function inputs validate before a request, preserve typed arguments and reviewed revisions', async () => {
  const ui = functions(); await ui.flush();
  button(ui.tree(), 'Run function').props.onClick(); await ui.flush();
  assert.equal(ui.posts().length, 0);
  assert.match(text(nodes(ui.tree()).find(node => node.props?.role === 'alert')), /Enter a value for amount/);
  fillArguments(ui);
  input(ui.tree(), 'Argument payload').props.onChange({ target: { value: '{bad' } }); ui.render();
  button(ui.tree(), 'Run function').props.onClick(); await ui.flush();
  assert.equal(ui.posts().length, 0); assert.equal(ui.notifications.length, 0);
  input(ui.tree(), 'Argument payload').props.onChange({ target: { value: '{"tags":["synthetic"]}' } }); ui.render();
  button(ui.tree(), 'Run function').props.onClick(); await ui.flush();
  assert.deepEqual(ui.posts()[0], ['post', '/object-types/qa-type/functions/update/invoke', { data: {
    modelRevision: 9, recordId: record.id, expectedRevision: 4,
    arguments: { amount: 2.5, payload: { tags: ['synthetic'] }, flag: false }, requestId: 'qa-request-1',
  } }]);
  assert.deepEqual(ui.committed, ['Synthetic update']);
  assert.match(text(ui.tree()), /Function completed/);
});

test('a failed function retry reuses its request ID, edited arguments receive a new ID', async () => {
  let attempts = 0;
  const ui = functions(async () => { attempts++; if (attempts < 3) throw new Error('Synthetic network failure'); return { name: 'Committed retry', stepCount: 3 }; });
  await ui.flush(); fillArguments(ui);
  for (let i = 0; i < 2; i++) { button(ui.tree(), 'Run function').props.onClick(); await ui.flush(); }
  assert.equal(ui.posts()[0][2].data.requestId, ui.posts()[1][2].data.requestId);
  assert.equal(input(ui.tree(), 'Argument amount').props.value, '2.5');
  assert.ok(ui.notifications.every(item => item.kind === 'error' && item.value.className === 'explorer-toast'));
  assert.deepEqual(ui.committed, []);
  input(ui.tree(), 'Argument amount').props.onChange({ target: { value: '3' } }); ui.render();
  button(ui.tree(), 'Run function').props.onClick(); await ui.flush();
  assert.notEqual(ui.posts()[2][2].data.requestId, ui.posts()[1][2].data.requestId);
  assert.deepEqual(ui.committed, ['Committed retry']);
});

test('function permissions, target selection and session ownership gate invocation and stale receipts', async () => {
  const denied = functions(undefined, { revision: 9, items: [] }); await denied.flush();
  assert.equal(button(denied.tree(), 'Run function'), undefined);
  assert.match(text(denied.tree()), /No published functions are available/);
  const absent = functions(); absent.props.record = undefined; await absent.flush();
  assert.equal(button(absent.tree(), 'Run function').props.disabled, true);
  button(absent.tree(), 'Run function').props.onClick(); await absent.flush(); assert.equal(absent.posts().length, 0);
  let finish;
  const ui = functions(() => new Promise(resolve => { finish = resolve; })); await ui.flush(); fillArguments(ui);
  const run = button(ui.tree(), 'Run function').props.onClick;
  run(); run(); ui.render(); assert.equal(ui.posts().length, 1);
  assert.equal(button(ui.tree(), 'Run function').props.disabled, true);
  ui.session.identity = 'another-account'; ui.render();
  finish({ name: 'Old account receipt', stepCount: 3 }); await ui.flush();
  assert.deepEqual(ui.committed, []); assert.equal(ui.notifications.length, 0);
  assert.doesNotMatch(text(ui.tree()), /Old account receipt|Function completed/);
  ui.unmount();
});

const objectType = { id: 'qa-type', definition: { name: 'Synthetic media', properties: [{ id: 'tags', name: 'Tags', type: 'json' }] } };
const history = { items: [{ kind: 'delete', revision: 5, createdAt: '2026-11-20T00:00:00Z', canUndo: true,
  changes: { tags: { before: ['synthetic'], after: null }, missing: { after: 'new' } } }], hasMore: false };

test('deleted objects show catalog, typed properties and a selected record history; restore refreshes the list', async () => {
  const reads = [], records = [{ ...record, revision: 5, properties: { tags: ['synthetic'] } }];
  let changed = 0;
  const ui = mount('PublishedDeletedObjects', { objectType, onChanged: () => changed++ }, {
    listOntologyRecords: async (...args) => { reads.push(args); return { items: records, hasMore: false }; },
  });
  await ui.flush();
  assert.deepEqual(reads[0].slice(0, 4), [objectType.id, 25, 0, true]);
  assert.equal(button(ui.tree(), 'Synthetic targetDeleted · Revision 5').props['aria-pressed'], true);
  const child = nodes(ui.tree()).find(node => typeof node.type === 'function' && node.type.name === 'RecordHistory');
  assert.equal(child.props.recordId, record.id);
  button(ui.tree(), 'Properties').props.onClick(); ui.render();
  assert.match(text(ui.tree()), /Tags\[\s*"synthetic"\s*\]/);
  button(ui.tree(), 'History and restore').props.onClick(); ui.render();
  const restored = mount('PublishedDeletedObjects', child.props, {
    listOntologyRecordHistory: async () => history,
    undoOntologyEdit: async (...args) => { assert.deepEqual(args, [objectType.id, record.id, 5]); records.length = 0; },
  }, 'RecordHistory');
  await restored.flush();
  assert.match(text(restored.tree()), /Not set/);
  await button(restored.tree(), 'Restore object').props.onClick(); await restored.flush(); await ui.flush();
  assert.equal(changed, 1); assert.equal(reads.length, 2);
  assert.match(text(ui.tree()), /No deleted objects are available to this account/);
  assert.equal(restored.notifications[0].kind, 'success');
});

test('restoration capability and failed restores preserve history without fabricating success', async () => {
  let writes = 0, changed = 0;
  const props = { objectType: objectType.id, recordId: record.id, onChanged: () => changed++ };
  const denied = mount('PublishedDeletedObjects', props, { listOntologyRecordHistory: async () => ({ ...history, items: [{ ...history.items[0], canUndo: false }] }) }, 'RecordHistory');
  await denied.flush(); assert.equal(button(denied.tree(), 'Restore object'), undefined);
  const ui = mount('PublishedDeletedObjects', props, {
    listOntologyRecordHistory: async () => history,
    undoOntologyEdit: async () => { writes++; throw new Error('Synthetic restore denied'); },
  }, 'RecordHistory');
  await ui.flush(); await button(ui.tree(), 'Restore object').props.onClick(); await ui.flush();
  assert.equal(writes, 1); assert.equal(changed, 0);
  assert.ok(button(ui.tree(), 'Restore object'));
  assert.equal(ui.notifications[0].kind, 'error'); assert.equal(ui.notifications[0].value.className, 'explorer-toast');
  ui.session.locked = true;
  await button(ui.tree(), 'Restore object').props.onClick(); await ui.flush();
  assert.equal(writes, 1);
});

test('a pending restore is submitted once and unmount suppresses its late notifications', async () => {
  let writes = 0, changed = 0, finish;
  const ui = mount('PublishedDeletedObjects', { objectType: objectType.id, recordId: record.id, onChanged: () => changed++ }, {
    listOntologyRecordHistory: async () => history,
    undoOntologyEdit: async () => { writes++; await new Promise(resolve => { finish = resolve; }); },
  }, 'RecordHistory');
  await ui.flush();
  const restore = button(ui.tree(), 'Restore object').props.onClick;
  const first = restore(), duplicate = restore();
  assert.equal(writes, 1);
  ui.render(); assert.equal(button(ui.tree(), 'Restore object').props.disabled, true);
  ui.unmount(); finish(); await Promise.all([first, duplicate]);
  assert.equal(changed, 0); assert.equal(ui.notifications.length, 0);
});

const definition = { id: 'synthetic', name: 'Initial', pluralName: 'Initials', description: '', projectId: 'qa-project',
  source: { mode: 'none' }, groups: [], primaryKey: ['id'], titleKey: 'id',
  properties: [{ id: 'id', name: 'Id', type: 'string', required: true }, { id: 'tags', name: 'Tags', type: 'json', required: false }],
  actions: [{ kind: 'modify', submitters: [], fields: ['tags'] }], functions: [],
  security: { mode: 'explicit', objectReaders: [], fieldReaders: {}, markings: [] } };

test('proposal section navigation retains edits across general, properties, actions and security', () => {
  const props = { initial: definition, definition, principals: [], onPrincipalSearch() {}, disabled: false };
  props.onChange = next => { props.definition = next; };
  const ui = mount('OntologyRevisionsEditor', props, {});
  const control = label => nodes(ui.tree()).find(node => node.type === 'label' && text(node).startsWith(label)).props.children;
  const field = label => nodes({ props: { children: control(label) } }).find(node => node.type === 'Select' || typeof node.type === 'function');
  field('Display name').props.onChange({ target: { value: 'Draft name' } }); ui.render();
  button(ui.tree(), 'Properties2').props.onClick(); ui.render();
  const names = nodes(ui.tree()).filter(node => node.type === 'label' && text(node) === 'Property name');
  nodes(names[1]).find(node => typeof node.type === 'function').props.onChange({ target: { value: 'Draft tags' } }); ui.render();
  button(ui.tree(), 'Actions1').props.onClick(); ui.render();
  assert.match(text(ui.tree()), /Edit objects/); assert.doesNotMatch(text(ui.tree()), /Modify objects/);
  button(ui.tree(), 'Security').props.onClick(); ui.render();
  input(ui.tree(), 'Object readers').props.onChange(['user:qa-reader']); ui.render();
  button(ui.tree(), 'General').props.onClick(); ui.render();
  assert.equal(field('Display name').props.value, 'Draft name');
  assert.equal(props.definition.properties[1].name, 'Draft tags');
  assert.deepEqual(props.definition.security.objectReaders, [{ kind: 'user', id: 'qa-reader' }]);
  assert.deepEqual(props.definition.actions, definition.actions);
  assert.equal(definition.name, 'Initial');
});

test('mapped proposal editors omit native actions and functions and preserve fixed source fields', () => {
  const mapped = { ...definition, source: { mode: 'existing', schema: 'ts_ods', table: 'synthetic' }, properties: definition.properties.map(p => ({ ...p, sourceColumn: p.id })) };
  const ui = mount('OntologyRevisionsEditor', { initial: mapped, definition: mapped, principals: [], onPrincipalSearch() {}, onChange() {}, disabled: false }, {});
  assert.equal(button(ui.tree(), 'Actions1'), undefined); assert.equal(button(ui.tree(), 'Functions0'), undefined);
  button(ui.tree(), 'Properties2').props.onClick(); ui.render();
  for (const label of ['Property ID', 'Source column']) for (const node of nodes(ui.tree()).filter(node => node.type === 'label' && text(node) === label)) {
    assert.equal(nodes(node).find(child => typeof child.type === 'function').props.disabled, true);
  }
});
