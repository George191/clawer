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
const neighbor = { linkId: 'synthetic-edge', canUnlink: true, record: { id: 'target-record', title: 'Synthetic neighbor', revision: 3, properties: {} } };
function graphLinks(mutate = async () => {}, canUnlink = true, direction = 'outgoing', canManage = false) {
  const writes = [], recordReads = [], moves = [];
  const ui = mount('OntologyGraph', 'Links', { anchor: { type: direction === 'outgoing' ? 'source-type' : 'target-type', record: 'anchor-record', title: 'Anchor' }, relationship: relation, direction, move: item => moves.push(item) }, {
    listOntologyRecords: async (...args) => { recordReads.push(args); return { items: [], hasMore: false }; },
    graphRequest: async (...args) => {
      if (args[0] !== 'get') { writes.push(args); return mutate(...args); }
      return args[1].endsWith('/link-types') ? { items: [relation], hasMore: false, canManage } : { items: [{ ...neighbor, canUnlink }], hasMore: false };
    },
  });
  return { ...ui, writes, recordReads, moves, select: () => ui.flush(), confirmation: () => nodes(ui.tree()).find(node => node.type === 'Modal') };
}

test('graph Links has no independent object picker or Add link action for managers', async () => {
  const ui = graphLinks(undefined, true, 'outgoing', true); await ui.select();
  assert.equal(button(ui.tree(), 'Add link'), undefined);
  assert.ok(!text(ui.tree()).includes('Record to link'));
  assert.equal(nodes(ui.tree()).filter(node => node.type === 'NativeSelect').length, 0);
  assert.deepEqual(ui.recordReads, []);
  button(ui.tree(), 'Synthetic neighbor').props.onClick();
  assert.deepEqual(ui.moves, [{ type: 'target-type', record: 'target-record', title: 'Synthetic neighbor' }]);
  assert.ok(button(ui.tree(), 'Remove link'));
  assert.equal(ui.writes.length, 0);
});

test('graph unlink honors per-edge capability and requires application confirmation', async () => {
  const denied = graphLinks(undefined, false); await denied.select();
  assert.equal(button(denied.tree(), 'Remove link'), undefined);
  const ui = graphLinks(); await ui.select();
  button(ui.tree(), 'Remove link').props.onClick(); ui.render();
  assert.equal(ui.confirmation().props.open, true); assert.equal(ui.writes.length, 0);
  ui.confirmation().props.onCancel(); ui.render();
  assert.equal(ui.confirmation().props.open, false); assert.equal(ui.writes.length, 0);
});

test('confirmed graph unlink preserves outgoing and incoming identities and blocks duplicate requests', async () => {
  for (const direction of ['outgoing', 'incoming']) {
    let finish;
    const ui = graphLinks(() => new Promise(resolve => { finish = resolve; }), true, direction); await ui.select();
    button(ui.tree(), 'Remove link').props.onClick(); ui.render();
    const confirm = ui.confirmation().props.onOk;
    const first = confirm(), duplicate = confirm();
    assert.deepEqual(ui.writes, [['delete', '/link-types/synthetic-link-type/links/synthetic-edge', { params: direction === 'outgoing'
      ? { sourceRecordId: 'anchor-record', targetRecordId: 'target-record' }
      : { sourceRecordId: 'target-record', targetRecordId: 'anchor-record' } }]]);
    ui.render(); assert.equal(ui.confirmation().props.confirmLoading, true);
    finish(); await Promise.all([first, duplicate]); await ui.flush();
    assert.equal(ui.confirmation().props.open, false);
  }
});

test('graph failures retain confirmation while locked sessions and late unmounted results are ignored', async () => {
  const failed = graphLinks(async () => { throw new Error('Synthetic edge changed'); }); await failed.select();
  button(failed.tree(), 'Remove link').props.onClick(); failed.render();
  await failed.confirmation().props.onOk(); await failed.flush();
  assert.equal(failed.confirmation().props.open, true);
  assert.ok(nodes(failed.tree()).some(node => typeof node.type === 'function' && node.type.name === 'Message' && node.props.text === 'Synthetic edge changed'));
  failed.session.locked = true; await failed.confirmation().props.onOk(); assert.equal(failed.writes.length, 1);
  let finish;
  const late = graphLinks(() => new Promise(resolve => { finish = resolve; })); await late.select();
  button(late.tree(), 'Remove link').props.onClick(); late.render();
  const pending = late.confirmation().props.onOk(); late.unmount(); finish(); await pending;
  assert.equal(late.notices.length, 0); assert.equal(late.writes.length, 1);
});

const objectType = { id: 'source-type', definition: { projectId: 'synthetic-project', name: 'Synthetic type', source: { mode: 'none' },
  properties: [{ id: 'id', name: 'ID', type: 'string', required: true }, { id: 'amount', name: 'Amount', type: 'number', required: false }] } };

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

test('graph lists relationship definitions first and removes them when the session locks', async () => {
  const reads = [];
  let pageResult;
  const ui = mount('OntologyGraph', 'RelationshipWorkbench', { objectType, recordId: 'anchor-record', recordTitle: 'Selected object', move() {} }, {
    graphRequest: async (...args) => {
      reads.push(args);
      if (pageResult) return pageResult();
      return { items: [relation], hasMore: true, canManage: true };
    },
  });
  await ui.flush();
  const catalog = input(ui.tree(), 'Ontology relationships');
  assert.ok(text(catalog).includes('Synthetic relation'));
  assert.ok(!text(catalog).includes('Selected object'));
  assert.ok(button(ui.tree(), 'New relationship'));
  assert.equal(reads.length, 1);
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
    input(ui.tree(), 'Step value id').props.onChange({ target: { value: 'synthetic-id' } }); ui.render();
    input(ui.tree(), 'Step value amount').props.onChange({ target: { value: '2.5' } }); ui.render();
    button(ui.tree(), 'Add step').props.onClick(); ui.render();
    input(ui.tree(), 'Transaction name').props.onChange({ target: { value: 'Synthetic transaction' } }); ui.render();
  } };
}

test('transaction staging validates permitted fields and commits nothing before explicit commit', async () => {
  const denied = transactions(undefined, []); await denied.flush();
  assert.equal(button(denied.tree(), 'Add step').props.disabled, true);
  assert.equal(input(denied.tree(), 'Step value id'), undefined);
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
