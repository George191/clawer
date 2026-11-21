import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const compile = name => ts.transpileModule(readFileSync(new URL('../src/pages/Pipeline/' + name, import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const workbenchCode = compile('ExplorerTableSchema.tsx');
const inspectorCode = compile('ExplorerPropertyInspector.tsx');
const jsx = (type, props) => ({ type, props });
const descendants = tree => tree && typeof tree === 'object' ? [tree, ...[tree.props?.children].flat(Infinity).flatMap(descendants)] : [];
const fixture = () => ({ schema: 'ts_ods', table: 'companies', version: 'v1', sourceDefinitionId: null, titleProperty: null,
  primaryKey: ['id'], groups: [], properties: [{ id: 'id', name: 'ID', description: 'Original', type: 'string', sourceColumn: 'id',
    sqlType: 'text', primaryKeyPosition: 1, visibility: 'normal', summary: false, group: null, status: 'active', formatter: 'default', presentation: 'value' }] });

function workbench(save = async (_schema, _table, mapping) => ({ ...mapping, version: 'v2' })) {
  let cursor = 0, tree;
  const states = [], refs = [], effects = [], scheduled = [], savedCalls = [], notifications = [];
  const counters = { reads: 0, onSaved: 0 };
  const exports = {}, session = { identity: 'reader', authenticated: true, locked: false };
  const props = { schema: 'ts_ods', table: 'companies', canManage: true, onSaved: () => counters.onSaved++ };
  const require = name => {
    if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx };
    if (name === 'react') return {
      useState(initial) { const index = cursor++; if (!(index in states)) states[index] = initial;
        return [states[index], value => { states[index] = typeof value === 'function' ? value(states[index]) : value; }]; },
      useRef(initial) { const index = cursor++; return refs[index] ??= { current: initial }; },
      useEffect(effect, deps) { const index = cursor++; if (!effects[index] || deps.some((dep, i) => !Object.is(dep, effects[index].deps[i]))) scheduled.push([index, effect, deps]); },
    };
    if (name === '@/components/ui') return { Button: 'Button', message: Object.fromEntries(['success', 'error'].map(kind => [kind, value => notifications.push({ kind, value })])) };
    if (name === '../../services/explorerProperties') return {
      fetchExplorerProperties: async () => { counters.reads++; return fixture(); },
      saveExplorerProperties: (...args) => { savedCalls.push(args); return save(...args); }, explorerPropertyType: () => 'string',
    };
    if (name === '../../services/api') return { fetchEtlTableColumns: async () => [{ name: 'id', type: 'text', primary: true, ordinalPosition: 1 }] };
    if (name === '../../services/session') return { getSessionState: () => session };
    if (name === '../../services/ontology') return { ontologyError: (error, fallback) => error.message || fallback };
    if (name === './SandboxMarkings') return { useSandboxIdentity: () => session.identity };
    if (name === './ExplorerPropertyInspector') return { default: 'Inspector', propertyFormatters: () => ['default'], readable: value => value, PropertyBadges: 'PropertyBadges' };
    if (name === '../../services/publishedExplorerHandoff') return { publishedExplorerHref: () => '/published' };
    if (name === 'react-router-dom') return { Link: 'Link' };
    if (name === '@/components/ui/icons') return { FilterOutlined: 'Filter', PlusOutlined: 'Plus', SearchOutlined: 'Search' };
    if (name.endsWith('.css')) return {};
    return { default: name };
  };
  vm.runInThisContext('(function(require,exports){' + workbenchCode + '\n})')(require, exports);
  const render = () => {
    cursor = 0; scheduled.length = 0;
    const root = exports.default(props);
    tree = root.type(root.props);
    for (const [index, effect, deps] of scheduled) {
      effects[index]?.cleanup?.(); effects[index] = { deps, cleanup: effect() };
    }
    return tree;
  };
  const flush = async () => { for (let i = 0; i < 5; i++) await Promise.resolve(); return render(); };
  render();
  return { render, flush, inspector: () => descendants(tree).find(node => node.type === 'Inspector'), savedCalls, notifications, counters,
    topChangeRows: () => descendants(tree).filter(node => node.props?.className === 'physical-property-changes') };
}

test('property edits save through the guarded callback and use standard toast without a top changes row', async () => {
  const ui = workbench(); await ui.flush();
  ui.inspector().props.onPatch({ description: 'Changed' }); ui.render();
  assert.equal(ui.inspector().props.dirty, true);
  assert.equal(ui.topChangeRows().length, 0);
  ui.inspector().props.onSave(); await ui.flush();
  assert.equal(ui.savedCalls.length, 1);
  assert.equal(ui.savedCalls[0][2].properties[0].description, 'Changed');
  assert.equal(ui.inspector().props.dirty, false);
  assert.equal(ui.notifications[0].kind, 'success');
  assert.equal(ui.notifications[0].value.className, 'explorer-toast');
  assert.equal(ui.counters.onSaved, 1);
});

test('invalid property metadata is rejected with a toast before any save request', async () => {
  const ui = workbench(); await ui.flush();
  ui.inspector().props.onPatch({ name: '' }); ui.render(); ui.inspector().props.onSave(); await ui.flush();
  assert.equal(ui.savedCalls.length, 0);
  assert.equal(ui.inspector().props.dirty, true);
  assert.equal(ui.notifications[0].kind, 'error');
  assert.match(ui.notifications[0].value.description, /unique ID/);
});

test('conflicts keep save blocked and discard reloads metadata', async () => {
  const ui = workbench(async () => { throw { message: 'Mapping changed', response: { status: 409 } }; }); await ui.flush();
  ui.inspector().props.onPatch({ description: 'Draft' }); ui.render(); ui.inspector().props.onSave(); await ui.flush();
  assert.equal(ui.inspector().props.conflict, true);
  ui.inspector().props.onSave(); await ui.flush();
  assert.equal(ui.savedCalls.length, 1);
  ui.inspector().props.onDiscard(); ui.render(); await ui.flush();
  assert.equal(ui.counters.reads, 2);
  assert.equal(ui.inspector().props.dirty, false);
  assert.equal(ui.inspector().props.property.description, 'Original');
});

test('busy saves reject duplicate submission and discard; removing the last property retains actions', async () => {
  let resolveSave;
  const ui = workbench((_schema, _table, mapping) => new Promise(resolve => { resolveSave = () => resolve(mapping); })); await ui.flush();
  ui.inspector().props.onPatch({ description: 'Draft' }); ui.render(); ui.inspector().props.onSave(); ui.inspector().props.onSave(); ui.render();
  assert.equal(ui.inspector().props.busy, true);
  ui.inspector().props.onDiscard(); ui.render();
  assert.equal(ui.inspector().props.property.description, 'Draft');
  assert.equal(ui.savedCalls.length, 1);
  resolveSave(); await ui.flush();
  assert.equal(ui.inspector().props.busy, false);
  ui.inspector().props.onRemove(); ui.render();
  assert.equal(ui.inspector().props.property, undefined);
  assert.equal(ui.inspector().props.dirty, true);
  ui.inspector().props.onDiscard(); ui.render();
  assert.equal(ui.inspector().props.property.id, 'id');
  assert.equal(ui.inspector().props.dirty, false);
});

function inspector(patch = {}) {
  const exports = {};
  const require = name => {
    if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx };
    if (name === 'react') return { useState: value => [value, () => {}] };
    if (name === 'react-router-dom') return { Link: 'Link' };
    if (name === '@/components/ui/icons') return Object.fromEntries(['DeleteOutlined','PlusOutlined','ReloadOutlined','SaveOutlined','UndoOutlined'].map(icon => [icon, icon]));
    if (name === './ColumnLineageReferences') return { canonColumn: () => false };
    if (name === './ExplorerResultLayout') return { columnPresentations: () => ['value'] };
    if (name === './SandboxIndexPreview') return {};
    return { default: name };
  };
  vm.runInThisContext('(function(require,exports){' + inspectorCode + '\n})')(require, exports);
  return exports.default({ mapping: fixture(), property: fixture().properties[0], columns: [], editable: true, dirty: true, busy: false, conflict: false,
    onSave() {}, onDiscard() {}, ...patch });
}

test('header controls remain icon-only, ordered and accessible across dirty and conflict states', () => {
  const actions = tree => descendants(tree).filter(node => node.type === 'button' && /Delete property|Save changes|Discard/.test(node.props?.['aria-label']));
  const visible = actions(inspector());
  assert.deepEqual(visible.map(button => button.props['aria-label']), ['Delete property ID', 'Save changes', 'Discard changes']);
  assert.ok(visible.every(button => typeof button.props.children !== 'string'));
  const pristine = actions(inspector({ dirty: false }));
  assert.equal(pristine[1].props.disabled, true);
  assert.equal(pristine[2].props.disabled, true);
  assert.ok(descendants(inspector({ dirty: false })).some(node => node.props?.className === 'physical-property-pending-actions' && node.props['aria-hidden']));
  const conflict = actions(inspector({ conflict: true }));
  assert.equal(conflict[1].props.disabled, true);
  assert.equal(conflict[2].props['aria-label'], 'Discard and reload');
  assert.equal(conflict[2].props.disabled, false);
  assert.deepEqual(actions(inspector({ property: undefined })).map(button => button.props['aria-label']), ['Save changes', 'Discard changes']);
});
