import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const code = ts.transpileModule(readFileSync(new URL('../src/pages/Pipeline/ExplorerRowLinks.tsx', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const jsx = (type, props) => ({ type, props });
const page = (direction = 'outgoing', canUnlink = true) => ({
  relations: [{ id: 'selector', relationId: '18', name: 'local_demo_expert_company', direction, kind: 'logical',
    schema: 'ts_ods', table: 'local_demo_companies', fields: ['company_id'], targetFields: ['id'], canLink: canUnlink }],
  selected: 'selector', links: [{ linkId: 'edge-instance', version: '7', identity: { id: 'LOCAL-CO-001' }, canUnlink }],
  identities: [{ id: 'LOCAL-CO-001' }], hasMore: false, offset: 0,
});

function workspace(initial = page(), fresh = initial) {
  let cursor = 0, tree, reads = 0, current = fresh, failure;
  const states = [], effects = [], scheduled = [], calls = [];
  const session = { authenticated: true, locked: false, identity: 'qa' };
  const exports = {};
  vm.runInThisContext('(function(require,exports){' + code + '\n})')(name => {
    if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx, Fragment: 'Fragment' };
    if (name === '@/components/ui') return { Button: 'Button' };
    if (name === '@/components/ui/icons') return { SettingOutlined: 'SettingsIcon' };
    if (name === './StorageRelationshipTypes') return { default: 'RelationshipManager' };
    if (name === 'react') return {
      useState(initial) { const index = cursor++; if (!(index in states)) states[index] = initial;
        return [states[index], value => { states[index] = typeof value === 'function' ? value(states[index]) : value; }]; },
      useEffect(effect, deps) { const index = cursor++; if (!effects[index] || deps.some((value, i) => !Object.is(value, effects[index].deps[i]))) scheduled.push([index, effect, deps]); },
    };
    if (name === '../../services/session') return { getSessionState: () => session };
    if (name === '../../services/api') return {
      fetchExplorerRowLinks: async () => { reads++; return current; },
      fetchExplorerRowByKey: async (_schema, _table, identity) => ({ row: { name: 'Physical company' }, identity, columnTypes: { name: 'text' } }),
      unlinkExplorerRowLink: async (...args) => { calls.push(args); if (failure) throw failure; current = { ...current, links: [], identities: [] }; },
    };
    if (name === '../../services/explorerProperties') return { fetchExplorerProperties: async () => ({ displayName: 'Physical companies', dataType: 'company', titleProperty: 'name', primaryKey: ['id'], properties: [] }) };
    if (name === '../../services/explorerLayouts') return { readLayouts: async () => ({ personal: { layout: null }, default: { layout: null } }), layoutPath: () => '/layout' };
    if (name === './ExplorerObjectOverview') return { explorerRowTitle: row => row.name, explorerFields: () => [], ExplorerTypeIcon: 'TypeIcon' };
    if (name === './ExplorerRelationshipsView') return { default: 'View' };
    if (name === './useExplorerErrorToast') return { default() {} };
    return { default: name };
  }, exports);
  const render = () => {
    cursor = 0; scheduled.length = 0;
    const root = exports.default({ schema: 'ts_ods', table: 'local_demo_experts', primaryKey: { id: 'LOCAL-EX-001' }, initialPage: initial });
    tree = root.type(root.props);
    for (const [index, effect, deps] of scheduled) { effects[index]?.cleanup?.(); effects[index] = { deps, cleanup: effect() }; }
    return tree;
  };
  const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); return render(); };
  render();
  return { flush, render, calls, tree: () => tree, reads: () => reads, reject: value => { failure = value; } };
}

for (const direction of ['outgoing', 'incoming']) test('storage unlink retains exact edge version and ' + direction + ' endpoints', async () => {
  const ui = workspace(page(direction)); await ui.flush();
  const signal = new AbortController().signal;
  assert.equal(ui.tree().props.records[0].key, 'edge-instance');
  await ui.tree().props.records[0].unlink.run(signal); ui.render(); await ui.flush();
  assert.deepEqual(ui.calls, [['ts_ods', 'local_demo_experts', 'edge-instance', {
    key: { id: 'LOCAL-EX-001' }, relationId: '18', direction, targetKey: { id: 'LOCAL-CO-001' }, expectedVersion: '7',
  }, signal]]);
  assert.equal(ui.tree().props.records.length, 0);
  assert.equal(ui.reads(), 2);
});

test('read-only storage capabilities do not expose unlink actions', async () => {
  const ui = workspace(page('outgoing', false)); await ui.flush();
  assert.equal(ui.tree().props.records[0].unlink, undefined);
  assert.equal(ui.calls.length, 0);
  assert.equal(ui.tree().props.management, undefined);
});

test('storage managers open the active relationship definition and refresh its links after saving', async () => {
  const ui = workspace(); await ui.flush();
  const management = () => ui.tree().props.management.props.children;
  assert.equal(management()[1].props.initialRelationId, '18');
  assert.equal(management()[1].props.open, false);
  management()[0].props.onClick(); ui.render();
  assert.equal(management()[1].props.open, true);
  management()[1].props.onChanged('18'); ui.render(); await ui.flush();
  assert.equal(ui.reads(), 2);
  assert.equal(ui.tree().props.selectedId, 'selector');
  management()[1].props.onClose(); ui.render();
  assert.equal(management()[1].props.open, false);
});

test('stale edge denial propagates without deleting the displayed relationship or reporting success', async () => {
  const ui = workspace(); await ui.flush();
  const denied = { response: { status: 409 }, message: 'Relationship changed' }; ui.reject(denied);
  await assert.rejects(ui.tree().props.records[0].unlink.run(new AbortController().signal), failure => failure === denied);
  ui.render(); await ui.flush();
  assert.equal(ui.tree().props.records[0].key, 'edge-instance');
  assert.equal(ui.reads(), 1);
});

test('reopening a prefetched link page verifies the stored edges instead of reviving a removed cached edge', async () => {
  const old = page(), fresh = { ...old, links: [], identities: [] };
  const ui = workspace(old, fresh); await ui.flush();
  assert.equal(ui.reads(), 1);
  assert.equal(ui.tree().props.records.length, 0);
});
