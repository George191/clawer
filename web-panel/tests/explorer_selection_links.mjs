import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const jsx = (type, props) => ({ type, props });
const nodes = tree => tree && typeof tree === 'object' ? [tree, ...[tree.props?.children].flat(Infinity).flatMap(nodes)] : [];
const text = tree => typeof tree === 'string' || typeof tree === 'number' ? String(tree) : tree && typeof tree === 'object' ? [tree.props?.children].flat(Infinity).map(text).join('') : '';
const compile = (file, require, globals) => {
  const source = ts.transpileModule(readFileSync(new URL('../src/pages/Pipeline/' + file, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const exports = {};
  const code = '(function(require,exports){' + source + '\n})';
  (globals ? vm.runInNewContext(code, globals) : vm.runInThisContext(code))(require, exports);
  return exports;
};

function selection(overrides = {}) {
  const states = [], refs = [], effects = [], scheduled = [];
  const calls = { choose: [], cancel: 0, refresh: 0, busy: [], selections: [] };
  let cursor = 0, tree;
  const source = { kind: 'backed', schema: 'ts_ods', table: 'parents' };
  const rows = [{ id: '9007199254740993', title: 'First' }, { id: '9007199254740995', title: 'Second' }];
  const props = { source, schema: source.schema, table: source.table, rows, columns: ['id', 'title'], dataColumns: [],
    metadata: { presentation: { displayName: 'Parents', groups: [] } }, rowKey: row => row.id, rowIdentity: row => ({ id: row.id }),
    rowVersion: () => '1', loading: false, locked: false, canLink: true,
    onChooseLinkTarget: context => calls.choose.push(context), onCancelLink: () => calls.cancel++,
    onLinkBusyChange: busy => calls.busy.push(busy), onRefresh: () => calls.refresh++,
    onSelectedRowsChange: rows => { calls.selections.push(rows); if (props.selectedRows !== undefined) props.selectedRows = rows; },
    renderRelationships: (row, page, onAddLink) => jsx('Relationships', { row, page, onAddLink }), ...overrides };
  const module = compile('ExplorerSelection.tsx', name => {
    if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx };
    if (name === 'react') return {
      useState(initial) { const i = cursor++; if (!(i in states)) states[i] = typeof initial === 'function' ? initial() : initial;
        return [states[i], next => { states[i] = typeof next === 'function' ? next(states[i]) : next; }]; },
      useRef(initial) { return refs[cursor++] ||= { current: initial }; }, useId() { cursor++; return 'selection-panel'; },
      useEffect(effect, deps) { const i = cursor++, previous = effects[i];
        if (!previous || !deps || deps.some((value, index) => !Object.is(value, previous.deps?.[index]))) {
          scheduled.push(() => { previous?.cleanup?.(); effects[i] = { deps, cleanup: effect() }; });
        }
      },
    };
    if (name === '@/components/ui') return { Button: 'Button', Checkbox: 'Checkbox', Table: 'Table', Dropdown: 'Dropdown', Input: 'Input', Select: 'Select', message: { warning() {} } };
    if (name === '@/components/ui/icons') return new Proxy({}, { get: (_, key) => String(key) });
    if (name === './ExplorerObjectOverview') return { ExplorerObjectOverview: 'Overview', ExplorerTypeIcon: 'TypeIcon',
      explorerRowTitle: row => row.title, explorerFields: () => [], propertyValue: (row, key) => row.record[key] };
    if (name === './ExplorerValue') return { ExplorerValueProvider: 'ValueProvider' };
    if (name === './ExplorerRowLinks') return { useExplorerLinkAvailability: () => ({ page: { relations: [], selected: null } }) };
    if (name === './explorerSelectionState') return { selectedPageRows: (previous, keys, next, preserve) => preserve ? [...previous.filter(row => !keys.has(row.key)), ...next] : next };
    if (name === './ExplorerLinkSelection') return { ExplorerLinkForm: 'ExplorerLinkForm',
      createLinkSelection: (source, sourceRow, relation) => ({ source, sourceRow, relation, sessionIdentity: 'test-user' }),
      storageLinkRelation: relation => relation, publishedLinkRelation: relation => relation };
    return { default() {} };
  });
  const render = () => { cursor = 0; tree = module.default(props); while (scheduled.length) scheduled.shift()(); return tree; };
  const find = predicate => nodes(tree).find(predicate);
  const button = label => find(node => node.type === 'Button' && text(node) === label);
  const table = () => find(node => node.type === 'Table');
  const select = chosen => { table().props.rowSelection.onChange(chosen.map(row => row.id), chosen, { type: 'all' }); render(); };
  render();
  return { calls, props, render, find, button, table, select, form: () => find(node => node.type === 'ExplorerLinkForm'), tree: () => tree };
}

test('Add link is adjacent to Compare and uses the existing selected objects inline', () => {
  const ui = selection(); ui.select(ui.props.rows);
  assert.ok(ui.button('Compare objects')); assert.ok(ui.button('Add link'));
  const header = ui.find(node => node.type === 'header' && text(node).includes('Selection Preview'));
  assert.deepEqual([header.props.children].flat(Infinity).filter(node => node?.type === 'Button').map(text), ['Compare objects', 'Add link', 'Clear selection']);
  ui.button('Compare objects').props.onClick(); ui.render();
  const comparison = ui.find(node => typeof node.type === 'function' && node.type.name === 'Comparison');
  assert.deepEqual(comparison.props.choices.map(row => row.identity.id), ui.props.rows.map(row => row.id));
  ui.button('Add link').props.onClick(); ui.render();
  assert.ok(ui.form()); assert.equal(ui.form().props.source, ui.props.source);
  assert.deepEqual(ui.form().props.choices.map(row => row.identity.id), ['9007199254740993', '9007199254740995']);
  assert.equal(ui.find(node => node.type === 'Modal'), undefined);
});

test('one selected source can hand off cross-type target selection without Compare or an object picker', () => {
  const ui = selection(); ui.select([ui.props.rows[0]]);
  assert.equal(ui.button('Compare objects'), undefined);
  ui.button('Add link').props.onClick(); ui.render();
  const context = { source: ui.props.source, sourceRow: ui.form().props.active,
    relation: { id: '7:outgoing', target: { kind: 'backed', schema: 'ts_ods', table: 'children' } } };
  ui.form().props.onChooseTarget(context);
  assert.equal(ui.calls.choose[0], context);
  assert.equal(ui.form().props.choices.length, 1);
});

test('pinned cross-type context opens the link form when a target row is selected', () => {
  const context = { source: { kind: 'backed', schema: 'ts_ods', table: 'elsewhere' }, sourceRow: { identity: { id: '1' }, key: '1', title: 'Pinned source' },
    relation: { id: '7:incoming', name: 'Belongs to', direction: 'incoming' } };
  const ui = selection({ linkContext: context });
  const banner = ui.find(node => node.props?.['aria-label'] === 'Relationship selection');
  assert.ok(text(banner).includes('Pinned source')); assert.ok(text(banner).includes('Belongs to · incoming'));
  assert.equal(ui.form(), undefined);
  ui.select([ui.props.rows[1]]);
  assert.equal(ui.form().props.context, context);
  assert.equal(ui.form().props.active.identity.id, '9007199254740995');
  assert.equal(ui.form().props.context.sourceRow.identity.id, '1');
});

test('a successful link cleans route context, refreshes and restores matching selected rows to Links', () => {
  const ui = selection(); ui.select(ui.props.rows);
  ui.button('Add link').props.onClick(); ui.render(); ui.form().props.onCreated(); ui.render();
  assert.equal(ui.calls.cancel, 1); assert.equal(ui.calls.refresh, 1);
  assert.equal(ui.form(), undefined); assert.ok(ui.find(node => node.type === 'Relationships'));
  ui.props.rows = [{ ...ui.props.rows[1], title: 'Second refreshed' }];
  ui.render(); ui.render();
  const relationships = ui.find(node => node.type === 'Relationships');
  assert.equal(relationships.props.row.title, 'Second refreshed');
  assert.deepEqual(ui.table().props.rowSelection.selectedRowKeys, ['9007199254740995']);
});

test('permission gating removes Add link while leaving two-object comparison available', () => {
  const ui = selection({ canLink: false }); ui.select(ui.props.rows);
  assert.equal(ui.button('Add link'), undefined); assert.ok(ui.button('Compare objects'));
  ui.button('Compare objects').props.onClick(); ui.render();
  assert.ok(ui.find(node => typeof node.type === 'function' && node.type.name === 'Comparison'));
});

test('a link write locks existing selection actions and checkbox changes until it settles', () => {
  const ui = selection(); ui.select(ui.props.rows);
  ui.button('Add link').props.onClick(); ui.render(); ui.form().props.onBusyChange(true); ui.render();
  assert.deepEqual(ui.calls.busy, [true]);
  for (const name of ['Compare objects', 'Add link', 'Clear selection']) assert.equal(ui.button(name).props.disabled, true);
  assert.equal(ui.table().props.rowSelection.getCheckboxProps(ui.props.rows[0]).disabled, true);
  ui.table().props.rowSelection.onChange([], [], { type: 'all' }); ui.render();
  assert.equal(ui.form().props.choices.length, 2);
  ui.form().props.onBusyChange(false); ui.render();
  assert.deepEqual(ui.calls.busy, [true, false]); assert.equal(ui.button('Compare objects').props.disabled, false);
});

test('the selection banner cancels the pinned context and returns to Overview', () => {
  const ui = selection({ linkContext: { sourceRow: { title: 'Pinned source' }, relation: { name: 'Owns', direction: 'outgoing' } } });
  ui.select([ui.props.rows[0]]);
  const banner = ui.find(node => node.props?.['aria-label'] === 'Relationship selection');
  nodes(banner).find(node => node.type === 'Button').props.onClick();
  ui.props.linkContext = undefined; ui.render();
  assert.equal(ui.calls.cancel, 1); assert.equal(ui.form(), undefined);
  assert.ok(ui.find(node => node.type === 'Overview'));
});

function linkRoute() {
  let session = { identity: 'test-user', authenticated: true, locked: false };
  const listeners = new Map(), navigations = [];
  const source = { kind: 'backed', schema: 'ts_ods', table: 'children' };
  const context = { source: { ...source, table: 'parents' }, sourceRow: { identity: { id: '9007199254740993' }, key: 'large-id', title: 'Parent' },
    relation: { id: '8:outgoing', relationId: '8', name: 'Owns', direction: 'outgoing', target: source, canLink: true }, sessionIdentity: session.identity };
  const location = { key: 'link-target', pathname: '/ontology/explorer', search: '?schema=ts_ods&table=children', hash: '#view', state: { linkSelection: context, existing: 'preserved' } };
  const services = { getSessionState: () => session, SESSION_CHANGED_EVENT: 'sessionChanged' };
  const helpers = compile('ExplorerLinkSelection.tsx', name => name === '../../services/session' ? services : {});
  const hook = compile('useExplorerLinkRoute.ts', name => {
    if (name === 'react') return { useEffect: effect => effect() };
    if (name === 'react-router-dom') return { useLocation: () => location, useNavigate: () => (...args) => navigations.push(args) };
    if (name === '../../services/session') return services;
    if (name === './ExplorerLinkSelection') return helpers;
    return {};
  }, { window: { addEventListener: (event, listener) => listeners.set(event, listener), removeEventListener: event => listeners.delete(event) } });
  return { context, source, location, navigations, render: target => hook.default(target ?? source),
    changeSession(next) { session = { ...session, ...next }; listeners.get('sessionChanged')?.(); } };
}

test('route context accepts only its exact target and cancellation preserves unrelated navigation state', () => {
  const route = linkRoute();
  assert.equal(route.render().context, route.context);
  assert.equal(route.render({ ...route.source, table: 'unrelated' }).context, undefined);
  route.render().finish();
  const [href, options] = route.navigations.at(-1);
  assert.equal(href, '/ontology/explorer?schema=ts_ods&table=children#view');
  assert.equal(options.replace, true); assert.equal(options.state.existing, 'preserved');
  assert.equal(options.state.linkSelection, undefined);
});

test('locking or changing accounts clears the pinned route context and prevents its reuse', () => {
  for (const next of [{ locked: true }, { identity: 'another-account' }, { authenticated: false }]) {
    const route = linkRoute(); route.render(); route.changeSession(next);
    assert.equal(route.navigations.length, 1);
    assert.equal(route.navigations[0][1].state.linkSelection, undefined);
    assert.equal(route.render().context, undefined);
  }
});
