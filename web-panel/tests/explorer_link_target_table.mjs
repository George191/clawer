import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const jsx = (type, props) => ({ type, props });
const nodes = tree => Array.isArray(tree) ? tree.flatMap(nodes) : tree && typeof tree === 'object'
  ? [tree, ...nodes(tree.props?.children)] : [];
const text = tree => typeof tree === 'string' || typeof tree === 'number' ? String(tree)
  : Array.isArray(tree) ? tree.map(text).join('') : tree && typeof tree === 'object' ? text(tree.props?.children) : '';
function compile(file, require) {
  const code = ts.transpileModule(readFileSync(new URL('../src/pages/Pipeline/' + file + '.tsx', import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const exports = {};
  vm.runInThisContext('(function(require,exports){' + code + '\n})')(require, exports);
  return exports;
}
const overview = compile('ExplorerObjectOverview', name => name === 'react' ? { lazy: () => 'Preview' }
  : name === 'react/jsx-runtime' ? { jsx, jsxs: jsx } : {});
const row = (key, title) => ({ key, title, identity: { id: key, tenant: 'canonical-tenant' },
  record: { id: 9007199254740992, tenant: 'display-tenant', name: title, active: false, note: null, payload: { tags: ['demo'] } },
  columnTypes: { id: 'bigint', active: 'boolean', payload: 'jsonb' } });
const module = compile('ExplorerLinkTargetTable', name => {
  if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx };
  if (name === '@/components/ui') return { Table: 'Table' };
  if (name === '@/components/ui/icons') return { FilterOutlined: 'FilterIcon' };
  if (name === './ExplorerObjectOverview') return overview;
  if (name === './ExplorerWorkbenchHeader') return { default: 'WorkbenchHeader' };
  if (name.endsWith('.css')) return {};
  throw new Error('Unexpected target table dependency ' + name);
});
function mount(overrides = {}) {
  const selected = [];
  const props = { source: { kind: 'backed', schema: 'ts_ods', table: 'target_people' }, label: 'Target people',
    rows: [row('9007199254740993', 'Alice'), row('9007199254740995', 'Bob')],
    columns: ['id', 'tenant', 'name', 'active', 'note', 'payload'], columnTypes: { id: 'bigint', active: 'boolean', payload: 'jsonb' },
    names: { name: 'Name' }, widths: { id: 240 }, glyph: jsx('TypeIcon', {}), selectedKey: '', loading: false, busy: false,
    onSelect: key => selected.push(key), ...overrides };
  let tree;
  const render = () => tree = module.default(props);
  render();
  return { props, selected, render, all: () => nodes(tree), table: () => nodes(tree).find(node => node.type === 'Table'),
    header: () => nodes(tree).find(node => node.type === 'WorkbenchHeader') };
}

test('target table reuses the Explorer header and keeps every toolbar action inert', () => {
  const ui = mount(), header = ui.header().props;
  const fieldset = ui.all().find(node => node.type === 'fieldset');
  assert.equal(fieldset.props.disabled, true); assert.equal(fieldset.props['aria-disabled'], 'true');
  assert.equal(fieldset.props.children.props.inert, '');
  assert.equal(header.source, ui.props.source); assert.equal(header.label, 'Target people');
  assert.equal(header.mode, 'results'); assert.equal(header.resource, 'preview'); assert.equal(header.admin, false);
  assert.deepEqual(header.availableResources, ['preview']); assert.deepEqual(header.selected, []);
  assert.equal(header.aggregate, undefined); assert.equal(header.onSearchValues('ignored'), false);
  for (const method of ['backToTypes', 'toggleCatalog', 'selectResource', 'setMode', 'openSavedItem', 'saveCompleted', 'updateSavedItem']) {
    assert.equal(header[method]('ignored'), undefined);
  }
  assert.equal(header.layoutControl.props.disabled, true); assert.equal(header.filterButton.props.disabled, true);
  assert.equal(header.rows[0].id, '9007199254740993'); assert.equal(header.rows[0].tenant, 'canonical-tenant');
  assert.equal(ui.props.rows[0].record.id, 9007199254740992);
});

test('all fields share the Explorer table while title and single selection columns stay frozen', () => {
  const ui = mount(), table = ui.table().props;
  assert.equal(table.className, 'flow-data-table'); assert.equal(table.pagination, false); assert.deepEqual(table.scroll, { x: 'max-content' });
  assert.equal(table.rowSelection.type, 'radio'); assert.equal(table.rowSelection.fixed, true); assert.equal(table.rowSelection.columnWidth, 36);
  assert.equal(table.columns.length, ui.props.columns.length + 1);
  assert.equal(table.columns[0].fixed, 'left'); assert.equal(table.columns[0].width, 232);
  assert.equal(table.columns[0].title, 'TITLE'); assert.equal(table.columns[1].width, 240);
  assert.equal(text(table.columns[3].title), 'Name');
  assert.equal(table.columns.some(column => column.sorter || column.filters), false);
  assert.equal(nodes(table.columns[0].render(undefined, ui.props.rows[0])).some(node => node.type === 'a' || node.type === 'button'), false);
  assert.equal(table.rowSelection.getCheckboxProps(ui.props.rows[0])['aria-label'], 'Select Alice');
});

test('physical and native cell rendering preserves exact identity and scalar JSON values', () => {
  const ui = mount(), columns = ui.table().props.columns;
  const rendered = field => text(columns.find(column => column.key === field).render(undefined, ui.props.rows[0]));
  assert.equal(rendered('id'), '9007199254740993'); assert.equal(rendered('tenant'), 'canonical-tenant');
  assert.equal(rendered('active'), 'false'); assert.equal(rendered('note'), 'null'); assert.equal(rendered('payload'), '{"tags":["demo"]}');
  const native = mount({ source: { kind: 'published', definitionId: 'equipment-type' }, label: 'Equipment',
    columns: ['id'], rows: [{ ...row('record-a', 'Native record'), identity: { $recordId: 'record-a' }, record: { id: 'business-id' } }] });
  assert.equal(native.header().props.source.definitionId, 'equipment-type');
  assert.equal(text(native.table().props.columns[1].render(undefined, native.props.rows[0])), 'business-id');
});

test('radio, row click and keyboard selection notify only current loaded targets', () => {
  const ui = mount(), table = ui.table().props, target = ui.props.rows[1];
  table.rowSelection.onChange([target.key]); table.onRow(target).onClick();
  const element = {}, event = key => ({ key, target: element, currentTarget: element, preventDefault() { this.prevented = true; } });
  const enter = event('Enter'); table.onRow(target).onKeyDown(enter);
  const space = event(' '); table.onRow(target).onKeyDown(space);
  table.rowSelection.onChange(['another-page']); table.onRow(target).onKeyDown({ ...event('Enter'), target: {} });
  assert.equal(enter.prevented, true); assert.equal(space.prevented, true);
  assert.deepEqual(ui.selected, Array(4).fill(target.key));
  ui.props.selectedKey = target.key; ui.render(); assert.deepEqual(ui.table().props.rowSelection.selectedRowKeys, [target.key]);
});

test('loading or a pending link blocks all target selection paths without losing its controlled key', () => {
  for (const state of ['loading', 'busy']) {
    const ui = mount({ [state]: true, selectedKey: '9007199254740993' }), table = ui.table().props, target = ui.props.rows[1];
    const element = {};
    table.rowSelection.onChange([target.key]); table.onRow(target).onClick();
    table.onRow(target).onKeyDown({ key: 'Enter', target: element, currentTarget: element, preventDefault() {} });
    assert.equal(table.rowSelection.getCheckboxProps(target).disabled, true); assert.equal(table.onRow(target).tabIndex, -1);
    assert.deepEqual(table.rowSelection.selectedRowKeys, ['9007199254740993']); assert.deepEqual(ui.selected, []);
  }
});

test('the target view follows saved wrap presentation without changing the saved layout', () => {
  const ui = mount({ wrap: true });
  assert.ok(ui.table().props.columns.slice(1).every(column => column.className === 'result-cell-wrap'));
  ui.props.wrap = false; ui.render();
  assert.ok(ui.table().props.columns.slice(1).every(column => column.className === 'result-cell-clip'));
});
