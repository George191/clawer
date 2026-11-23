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
function mount(file, name, props, api) {
  const states = [], refs = [], effects = [], scheduled = [], notices = [], routes = [];
  const session = { identity: 'synthetic-user', authenticated: true, locked: false };
  let cursor = 0, tree, requestId = 0;
  const compile = filename => ts.transpileModule(readFileSync(new URL('../src/pages/Pipeline/' + filename + '.tsx', import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const parser = ts.transpileModule(readFileSync(new URL('../src/pages/Pipeline/ontologyPropertyValue.ts', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  const values = {}, exports = {};
  vm.runInThisContext('(function(exports){' + parser + '\n})')(values);
  const require = dependency => {
    if (dependency === 'react/jsx-runtime') return { jsx, jsxs: jsx, Fragment: 'Fragment' };
    if (dependency === 'react') return {
      useState(initial) { const i = cursor++; if (!(i in states)) states[i] = typeof initial === 'function' ? initial() : initial;
        return [states[i], next => { states[i] = typeof next === 'function' ? next(states[i]) : next; }]; },
      useRef(initial) { return refs[cursor++] ||= { current: initial }; },
      useEffect(effect, deps) { const i = cursor++; if (!effects[i] || !deps || deps.some((value, j) => !Object.is(value, effects[i].deps[j]))) scheduled.push([i, effect, deps]); },
    };
    if (dependency === '@/components/ui') return { Button: 'Button', Modal: 'Modal', NativeSelect: 'NativeSelect', Select: 'Select', message: Object.fromEntries(['success', 'error'].map(kind => [kind, value => notices.push({ kind, value })])) };
    if (dependency === '@/components/ui/icons') return new Proxy({}, { get: (_, key) => String(key) });
    if (dependency === '../../services/session') return { getSessionState: () => session };
    if (dependency === '../../services/ontology') return { ...api, ontologyError: (error, fallback) => error.message || fallback, newOntologyRequestId: () => 'synthetic-request-' + ++requestId };
    if (dependency === '../../services/ontology_graph') return api;
    if (dependency === './ontologyPropertyValue') return values;
    if (dependency === './useExplorerErrorToast') return { default() {} };
    if (dependency === 'react-router-dom') return { useNavigate: () => href => routes.push(href) };
    if (dependency === '../../services/publishedExplorerHandoff') return { publishedExplorerHref: () => '/synthetic-explorer' };
    return { default: dependency };
  };
  vm.runInThisContext('(function(require,exports){' + compile(file) + (name ? '\nexports.component=' + name + ';' : '\nexports.component=exports.default;') + '\n})')(require, exports);
  const render = () => {
    cursor = 0; scheduled.length = 0; tree = exports.component(props);
    for (const [i, effect, deps] of scheduled) { effects[i]?.cleanup?.(); effects[i] = { deps, cleanup: effect() }; }
    return tree;
  };
  const flush = async () => { for (let i = 0; i < 7; i++) { await Promise.resolve(); render(); } return tree; };
  render();
  return { props, session, notices, routes, render, flush, tree: () => tree, unmount: () => effects.forEach(effect => effect?.cleanup?.()) };
}

const relation = { id: 'synthetic-link-type', key: 'synthetic_relation', cardinality: 'many_to_many', name: 'Synthetic relation', sourceDefinitionId: 'source-type', targetDefinitionId: 'target-type' };
const objectType = { id: 'source-type', definition: { projectId: 'synthetic-project', name: 'Synthetic type', source: { mode: 'none' },
  properties: [{ id: 'id', name: 'ID', type: 'string', required: true }, { id: 'amount', name: 'Amount', type: 'number', required: false },
    { id: 'note', name: 'Note', type: 'string', required: false }] } };

test('new relationship type submission is single-flight and cannot notify after unmount', async () => {
  const writes = []; let finish, saved = 0;
  const ui = mount('OntologyGraph', 'NewRelationship', { object: objectType, onSaved: () => saved++ }, {
    graphRequest: async (...args) => {
      if (args[0] === 'get') return { items: [{ id: 'target-type', name: 'Target type' }], hasMore: false };
      writes.push(args); return new Promise(resolve => { finish = resolve; });
    },
  });
  await ui.flush();
  for (const [label, value] of [['Relationship name', 'Synthetic relation'], ['Relationship key', 'synthetic_relation']]) {
    const node = nodes(ui.tree()).find(item => item.type === 'label' && text(item).startsWith(label));
    nodes(node).find(item => item.type === 'input').props.onChange({ target: { value } }); ui.render();
  }
  input(ui.tree(), 'New relationship target type').props.onChange('target-type');
  ui.render();
  const submit = ui.tree().props.onSubmit;
  submit({ preventDefault() {} }); submit({ preventDefault() {} });
  assert.equal(writes.length, 1);
  assert.deepEqual(writes[0][2].data, { sourceDefinitionId: 'source-type', targetDefinitionId: 'target-type', key: 'synthetic_relation', name: 'Synthetic relation', cardinality: 'many_to_many' });
  ui.unmount(); finish({}); await Promise.resolve(); await Promise.resolve();
  assert.equal(saved, 0); assert.equal(ui.notices.length, 0);
});

test('incoming relationship creation reverses endpoints without changing cardinality', async () => {
  const writes = [], saved = [];
  const ui = mount('OntologyGraph', 'NewRelationship', { object: objectType, onSaved: value => saved.push(value) }, {
    graphRequest: async (...args) => {
      if (args[0] === 'get') return { items: [{ id: 'target-type', name: 'Target type' }], hasMore: false };
      writes.push(args);
      return relation;
    },
  });
  await ui.flush();
  input(ui.tree(), 'New relationship direction').props.onChange({ target: { value: 'incoming' } });
  ui.render();
  input(ui.tree(), 'New relationship source type').props.onChange('target-type');
  ui.render();
  ui.tree().props.onSubmit({ preventDefault() {} });
  await ui.flush();
  assert.deepEqual(writes[0][2].data, { sourceDefinitionId: 'target-type', targetDefinitionId: 'source-type', key: '', name: '', cardinality: 'many_to_many' });
  assert.deepEqual(saved, [relation]);
});

test('relationship management reads definitions only and removes them when the session locks', async () => {
  const reads = [];
  let pageResult;
  const ui = mount('OntologyGraph', 'RelationshipWorkbench', { objectType }, {
    graphRequest: async (...args) => {
      reads.push(args);
      if (pageResult) return pageResult();
      return { items: [relation], hasMore: true, canManage: true };
    },
  });
  await ui.flush();
  const catalog = input(ui.tree(), 'Ontology relationships');
  assert.ok(text(catalog).includes('Synthetic relation'));
  assert.ok(!text(ui.tree()).includes('Starting object'));
  assert.ok(!text(ui.tree()).includes('Connected objects'));
  assert.equal(button(ui.tree(), 'Remove link'), undefined);
  assert.equal(button(ui.tree(), 'Dynamic object sets'), undefined);
  assert.ok(nodes(ui.tree()).some(node => typeof node.type === 'function' && node.type.name === 'RelationshipDefinition'));
  assert.ok(button(ui.tree(), 'New relationship'));
  assert.equal(reads.length, 1);
  assert.equal(reads[0][1], '/object-types/source-type/link-types');
  assert.deepEqual(reads[0][2].params, { limit: 20, offset: 0 });
  ui.session.locked = true;
  ui.render();
  assert.ok(reads[0][2].signal.aborted);
  assert.ok(!text(input(ui.tree(), 'Ontology relationships')).includes('Synthetic relation'));
  assert.equal(button(ui.tree(), 'New relationship'), undefined);
  assert.equal(reads.length, 1);
  pageResult = () => ({ items: [], hasMore: false, canManage: false });
  ui.session.identity = 'other-user'; ui.session.locked = false;
  ui.render();
  await ui.flush();
  assert.ok(text(input(ui.tree(), 'Ontology relationships')).includes('No relationships'));
  assert.equal(button(ui.tree(), 'New relationship'), undefined);
});

test('named object sets mount separately and remain scoped to ontology-native object types', () => {
  for (const mode of ['none', 'existing']) {
    const object = { ...objectType, definition: { ...objectType.definition, source: { mode } } };
    const props = { object, returnTo: '/synthetic', onOpenExplorer() {} };
    const ui = mount('OntologyGraph', 'DynamicObjectSets', props, {});
    const sets = nodes(ui.tree()).find(node => typeof node.type === 'function' && node.type.name === 'ObjectSets');
    if (mode === 'none') {
      assert.ok(sets);
      assert.equal(sets.props.returnTo, props.returnTo);
    } else assert.equal(ui.tree(), null);
  }
});

test('saved set deletion uses explicit confirmation and its guard blocks duplicate or stale-session writes', async () => {
  const writes = []; let finish;
  const ui = mount('OntologyGraph', 'ObjectSets', { object: objectType, returnTo: '/synthetic', onOpenExplorer() {} }, {
    graphRequest: async (...args) => {
      if (args[0] === 'get') return { items: [{ id: 'synthetic-set', name: 'Synthetic saved set' }], hasMore: false };
      writes.push(args); return new Promise(resolve => { finish = resolve; });
    },
  });
  await ui.flush();
  const saved = nodes(ui.tree()).find(item => item.type === 'label' && text(item).startsWith('Saved set'));
  nodes(saved).find(item => item.type === 'NativeSelect').props.onChange({ target: { value: 'synthetic-set' } }); ui.render();
  button(ui.tree(), 'Delete saved set').props.onClick(); ui.render();
  const confirmation = () => nodes(ui.tree()).find(item => item.type === 'Modal');
  assert.equal(writes.length, 0); assert.equal(confirmation().props.open, true);
  confirmation().props.onCancel(); ui.render(); assert.equal(confirmation().props.open, false);
  button(ui.tree(), 'Delete saved set').props.onClick(); ui.render();
  const confirm = confirmation().props.onOk;
  ui.session.locked = true; await confirm(); assert.equal(writes.length, 0);
  ui.session.locked = false;
  const first = confirm(), duplicate = confirm(); assert.equal(writes.length, 1);
  assert.deepEqual(writes[0].slice(0, 2), ['delete', '/object-types/source-type/sets/synthetic-set']);
  ui.session.identity = 'another-user'; finish({}); await Promise.all([first, duplicate]); ui.render();
  assert.equal(confirmation().props.open, true); assert.equal(ui.notices.length, 0);
  ui.unmount();
});

const stepField = (tree, id) => nodes(tree).find(node => node.type === './ExplorerFieldEditor' && node.props.inputLabel === 'Step value ' + id);

function transactions(apply = async () => ({ id: 'synthetic-transaction', name: 'Synthetic transaction' }), actions = [{ kind: 'create', fields: ['id', 'amount'] }]) {
  const writes = [], undos = [], committed = [];
  const ui = mount('OntologyTransactions', '', { objectType, onCommitted: () => committed.push(true) }, {
    listPublishedOntologyTypes: async () => ({ items: [objectType], hasMore: false }),
    listOntologyRecords: async () => ({ items: [], hasMore: false, actions }),
    listTransactions: async () => ({ items: [], hasMore: false }),
    applyTransaction: async (...args) => { writes.push(args); return apply(...args); },
    undoTransaction: async (...args) => { undos.push(args); },
  });
  return { ...ui, writes, undos, committed, async stage() {
    await ui.flush();
    stepField(ui.tree(), 'id').props.onChange('synthetic-id'); ui.render();
    stepField(ui.tree(), 'amount').props.onChange('2.5'); ui.render();
    button(ui.tree(), 'Add step').props.onClick(); ui.render();
    input(ui.tree(), 'Transaction name').props.onChange({ target: { value: 'Synthetic transaction' } }); ui.render();
  } };
}

test('transaction staging validates permitted fields and commits nothing before explicit commit', async () => {
  const denied = transactions(undefined, []); await denied.flush();
  assert.equal(button(denied.tree(), 'Add step').props.disabled, true);
  assert.equal(stepField(denied.tree(), 'id'), undefined);
  const ui = transactions(); await ui.flush();
  button(ui.tree(), 'Add step').props.onClick(); ui.render();
  assert.match(text(ui.tree()), /No staged changes/); assert.equal(ui.writes.length, 0);
  await ui.stage();
  assert.equal(ui.writes.length, 0);
  assert.match(text(ui.tree()), /Create object/);
  button(ui.tree(), 'History and writeback').props.onClick(); ui.render();
  button(ui.tree(), 'New transaction · 1').props.onClick(); ui.render();
  assert.equal(input(ui.tree(), 'Transaction name').props.value, 'Synthetic transaction');
  await button(ui.tree(), 'Commit transaction').props.onClick(); await ui.flush();
  assert.deepEqual(ui.writes, [['synthetic-project', 'Synthetic transaction', 'synthetic-request-1', [
    { kind: 'create', definitionId: 'source-type', alias: '@item1', values: { id: 'synthetic-id', amount: 2.5 } },
  ]]]);
  assert.equal(ui.committed.length, 1);
});

test('optional transaction fields distinguish NULL, untouched and restored values on commit', async () => {
  const ui = transactions(); await ui.flush();
  const field = id => stepField(ui.tree(), id).props;
  assert.equal(field('id').nullControl, undefined);
  assert.equal(nodes(ui.tree()).some(node => node.type === 'input' && node.props.type === 'checkbox'), false);
  for (const scenario of ['untouched', 'restored', 'null']) {
    field('id').onChange(scenario); ui.render();
    if (scenario !== 'untouched') { field('amount').onChange('7.5'); ui.render(); }
    field('amount').nullControl.onChange(true); ui.render();
    assert.equal(field('amount').nullControl.checked, true);
    if (scenario !== 'null') { field('amount').nullControl.onChange(false); ui.render(); }
    button(ui.tree(), 'Add step').props.onClick(); ui.render();
    assert.equal(field('amount').nullControl.checked, false);
  }
  input(ui.tree(), 'Transaction name').props.onChange({ target: { value: 'Nullable fields' } }); ui.render();
  await button(ui.tree(), 'Commit transaction').props.onClick();
  assert.deepEqual(ui.writes[0][3].map(step => step.values), [
    { id: 'untouched' }, { id: 'restored', amount: 7.5 }, { id: 'null', amount: null },
  ]);
});

test('clearing transaction text submits an empty string unless Set NULL is active', async () => {
  const ui = transactions(undefined, [{ kind: 'create', fields: ['id', 'note'] }]);
  await ui.flush();
  const note = () => stepField(ui.tree(), 'note').props;
  for (const id of ['cleared', 'restored', 'null']) {
    stepField(ui.tree(), 'id').props.onChange(id); ui.render();
    note().onChange('Draft'); ui.render();
    note().onChange(''); ui.render();
    if (id !== 'cleared') { note().nullControl.onChange(true); ui.render(); }
    if (id === 'restored') { note().nullControl.onChange(false); ui.render(); }
    button(ui.tree(), 'Add step').props.onClick(); ui.render();
  }
  input(ui.tree(), 'Transaction name').props.onChange({ target: { value: 'Clear text' } }); ui.render();
  await button(ui.tree(), 'Commit transaction').props.onClick();
  assert.deepEqual(ui.writes[0][3].map(step => step.values), [
    { id: 'cleared', note: '' }, { id: 'restored', note: '' }, { id: 'null', note: null },
  ]);
});

test('failed transaction commits retain staged changes and reuse the idempotency key on retry', async () => {
  let attempts = 0;
  const ui = transactions(async () => { if (++attempts === 1) throw new Error('Synthetic connection failure'); return { name: 'Synthetic transaction' }; });
  await ui.stage();
  await button(ui.tree(), 'Commit transaction').props.onClick(); await ui.flush();
  assert.equal(ui.committed.length, 0); assert.equal(input(ui.tree(), 'Transaction name').props.value, 'Synthetic transaction');
  assert.equal(ui.notices.at(-1).value.className, 'explorer-toast');
  await button(ui.tree(), 'Commit transaction').props.onClick(); await ui.flush();
  assert.equal(ui.writes.length, 2); assert.equal(ui.writes[0][2], ui.writes[1][2]);
  assert.deepEqual(ui.writes[0][3], ui.writes[1][3]); assert.equal(ui.committed.length, 1);
});

test('transaction guards reject duplicate commits, locked sessions and late completion after account change', async () => {
  let finish;
  const ui = transactions(() => new Promise(resolve => { finish = resolve; })); await ui.stage();
  const commit = button(ui.tree(), 'Commit transaction').props.onClick;
  ui.session.locked = true; await commit(); assert.equal(ui.writes.length, 0);
  ui.session.locked = false;
  const first = commit(), duplicate = commit(); assert.equal(ui.writes.length, 1);
  ui.render(); assert.equal(button(ui.tree(), 'Commit transaction').props.disabled, true);
  ui.session.identity = 'another-user'; finish({ name: 'Stale transaction' }); await Promise.all([first, duplicate]);
  assert.equal(ui.committed.length, 0); assert.equal(ui.notices.length, 0);
  ui.unmount();
});
