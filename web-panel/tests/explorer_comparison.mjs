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
const row = (key, name) => ({ key, title: name, identity: { id: key }, columnTypes: {},
  record: { name, status: 'Active', payload: { steps: [1, 2] } } });

function mount(kind = 'Comparison') {
  const states = [], refs = [], effects = [], scheduled = [];
  let cursor = 0, tree;
  const choices = [row('a', 'Alice'), row('b', 'Bob'), row('c', 'Chris')];
  const props = kind === 'Comparison' ? { columns: ['name', 'status', 'payload'], choices }
    : { schema: 'ts_ods', table: 'people', rows: choices.map(item => item.record), columns: ['name'], dataColumns: [],
      metadata: { presentation: { displayName: 'People', groups: [] } },
      rowKey: record => record.name, rowIdentity: record => ({ name: record.name }), rowVersion: () => '1',
      loading: false, locked: false, renderRelationships: () => jsx('Relationships', {}) };
  const code = ts.transpileModule(readFileSync(new URL('../src/pages/Pipeline/ExplorerSelection.tsx', import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const exports = {};
  vm.runInThisContext('(function(require,exports){' + code + '\nexports.Comparison=Comparison;\n})')(name => {
    if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx, Fragment: 'Fragment' };
    if (name === 'react') return {
      useState(initial) { const i = cursor++; if (!(i in states)) states[i] = typeof initial === 'function' ? initial() : initial;
        return [states[i], value => { states[i] = typeof value === 'function' ? value(states[i]) : value; }]; },
      useRef(initial) { return refs[cursor++] ||= { current: initial }; }, useId() { cursor++; return 'comparison'; },
      useEffect(effect, deps) { const i = cursor++;
        if (!effects[i] || !deps || deps.some((value, index) => !Object.is(value, effects[i].deps?.[index]))) scheduled.push([i, effect, deps]); },
    };
    if (name === '@/components/ui') return { Button: 'Button', Checkbox: 'Checkbox', Table: 'Table', Select: 'Select', Input: 'Input', Dropdown: 'Dropdown', message: { warning() {} } };
    if (name === '@/components/ui/icons') return new Proxy({}, { get: (_, key) => String(key) });
    if (name === './ExplorerObjectOverview') return {
      ExplorerObjectOverview: 'Overview', ExplorerTypeIcon: 'TypeIcon', ExplorerFieldValue: 'FieldValue', explorerRowTitle: record => record.name,
      propertyValue: (item, field) => item.record[field], explorerFields: (_item, fields) => fields.map(field => ({ field })),
    };
    if (name === './ExplorerRowLinks') return { useExplorerLinkAvailability: () => ({ page: { relations: [], selected: null } }) };
    if (name === './ExplorerValue') return { ExplorerValueProvider: 'ValueProvider' };
    if (name === './explorerSelectionState') return { selectedPageRows: (_previous, _keys, selected) => selected };
    return { default() {} };
  }, exports);
  const render = () => {
    cursor = 0; scheduled.length = 0; tree = (kind === 'Comparison' ? exports.Comparison : exports.default)(props);
    for (const [i, effect, deps] of scheduled) { effects[i]?.cleanup?.(); effects[i] = { deps, cleanup: effect() }; }
    return tree;
  };
  const all = () => nodes(tree);
  render();
  return { props, render, all, tree: () => tree, select: label => all().find(item => item.type === 'Select' && item.props['aria-label'] === label),
    button: label => all().find(item => item.type === 'Button' && text(item) === label) };
}

test('comparison uses Current object and Target object for the existing selected pair', () => {
  const ui = mount(), source = ui.select('Current object'), target = ui.select('Target object');
  assert.equal(source.props.value, 'a'); assert.equal(target.props.value, 'b');
  assert.equal(source.props.options.length, 3); assert.equal(target.props.options.length, 3);
  assert.equal(source.props.options.find(item => item.value === 'b').disabled, true);
  assert.equal(target.props.options.find(item => item.value === 'a').disabled, true);
  assert.equal(ui.select('First object'), undefined); assert.equal(ui.select('Second object'), undefined);
});

test('switching the compared objects uses only selected rows and keeps the two sides distinct', () => {
  const ui = mount(); ui.select('Current object').props.onChange('c'); ui.render();
  assert.equal(ui.select('Current object').props.value, 'c'); assert.equal(ui.select('Target object').props.value, 'b');
  assert.deepEqual(ui.all().filter(item => item.type === 'thead').map(text), ['PropertyChrisBob']);
  ui.props.choices = ui.props.choices.filter(item => item.key !== 'c'); ui.render();
  assert.equal(ui.select('Current object').props.value, 'a'); assert.equal(ui.select('Target object').props.value, 'b');
});

test('Differences only still hides equal scalar and nested properties', () => {
  const ui = mount(); assert.equal(ui.all().find(item => item.type === 'tbody').props.children.length, 3);
  ui.all().find(item => item.type === 'Checkbox').props.onChange({ target: { checked: true } }); ui.render();
  const body = ui.all().find(item => item.type === 'tbody');
  assert.equal(body.props.children.length, 1); assert.ok(text(body).includes('name'));
});

test('table selection retains Compare and Links without a separate multi-object Add link workflow', () => {
  const ui = mount('Selection');
  const choose = rows => { ui.all().find(item => item.type === 'Table').props.rowSelection.onChange(rows.map(row => row.name), rows, { type: 'all' }); ui.render(); };
  choose(ui.props.rows); assert.ok(ui.button('Compare objects')); assert.equal(ui.button('Add link'), undefined);
  choose([ui.props.rows[0]]); assert.equal(ui.button('Compare objects'), undefined); assert.equal(ui.button('Add link'), undefined);
  const links = ui.all().find(item => item.type === 'button' && text(item).includes('Links'));
  links.props.onClick(); ui.render(); assert.ok(ui.all().find(item => item.type === 'Relationships'));
});
