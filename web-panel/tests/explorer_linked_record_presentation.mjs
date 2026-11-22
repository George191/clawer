import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const jsx = (type, props) => ({ type, props });
const nodes = tree => tree && typeof tree === 'object' ? [tree, ...[tree.props?.children].flat(Infinity).flatMap(nodes)] : [];
const compile = (file, require, component) => {
  const source = ts.transpileModule(readFileSync(new URL('../src/pages/Pipeline/' + file + '.tsx', import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const exports = {};
  vm.runInThisContext('(function(require,exports){' + source + (component ? '\nexports.component=' + component + ';' : '') + '\n})')(require, exports);
  return exports;
};
const overview = compile('ExplorerObjectOverview', name => name === 'react' ? { lazy: () => 'Preview' }
  : name === 'react/jsx-runtime' ? { jsx, jsxs: jsx } : {});
const profile = name => ({ displayName: name, titleProperty: 'caption', primaryKey: ['id'], dataType: 'media', properties: [
  { id: 'caption', sourceColumn: 'caption', name: name + ' title', visibility: 'normal', type: 'string', formatter: 'default' },
  { id: 'secret', sourceColumn: 'secret', name: 'Hidden field', visibility: 'hidden', type: 'string', formatter: 'default' },
] });
const physical = profile('Physical media'), published = profile('Published media');
const layouts = { personal: { layout: { columns: [{ field: 'caption', presentation: 'value' }] } }, default: { layout: null } };

function mount(kind, requests = {}) {
  const state = [], refs = [], effects = [], scheduled = [], calls = [], errors = [];
  const session = { identity: 'synthetic', authenticated: true, locked: false };
  let cursor = 0, tree;
  const props = kind === 'backed' ? { schema: 'ts_ods', table: 'anchor', primaryKey: { id: 'anchor' } } : { definitionId: 'anchor-type', recordId: 'anchor' };
  const relation = { id: 'relation', relationId: '12', name: 'Owns', direction: 'outgoing', schema: 'ts_ods', table: 'media', targetDefinitionId: 'target-type', fields: ['id'], targetFields: ['id'] };
  const page = kind === 'backed'
    ? { relations: [relation], selected: 'relation', links: [{ linkId: 'edge', version: '3', identity: { id: '9007199254740993' }, canUnlink: true }], offset: 0, hasMore: false }
    : { relations: [relation], selected: 'relation:outgoing', items: [{ id: 'native-record', linkId: 'edge', canUnlink: true, title: 'Native title', properties: { caption: 'A property', secret: 'hidden' } }], offset: 0, hasMore: false };
  const api = {
    fetchExplorerRowLinks: async () => page,
    fetchExplorerRowByKey: async () => ({ row: { caption: 'Physical title', secret: 'hidden' }, identity: { id: '9007199254740993' }, columnTypes: { caption: 'text', secret: 'text' } }),
    fetchExplorerProperties: async () => physical,
    fetchPublishedRelationships: async () => page,
    fetchPublishedOntologyType: async () => ({ id: 'target-type', definition: { name: published.displayName } }),
    readLayouts: async () => layouts,
    ...requests,
  };
  const tracked = Object.fromEntries(Object.entries(api).map(([name, run]) => [name, (...args) => { calls.push({ name, args }); return run(...args); }]));
  const require = name => {
    if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx, Fragment: 'Fragment' };
    if (name === '@/components/ui') return { Button: 'Button' };
    if (name === '@/components/ui/icons') return { SettingOutlined: 'SettingsIcon' };
    if (name === './StorageRelationshipTypes') return { default: 'RelationshipManager' };
    if (name === 'react') return {
      useState(initial) { const i = cursor++; if (!(i in state)) state[i] = initial; return [state[i], next => { state[i] = typeof next === 'function' ? next(state[i]) : next; }]; },
      useRef(initial) { return refs[cursor++] ||= { current: initial }; },
      useEffect(effect, deps) { const i = cursor++; if (!effects[i] || deps.some((value, j) => !Object.is(value, effects[i].deps[j]))) scheduled.push([i, effect, deps]); },
    };
    if (name === '../../services/session') return { getSessionState: () => session };
    if (name === './useExplorerErrorToast') return { default: (...args) => errors.push(args) };
    if (name === './ExplorerObjectOverview') return overview;
    if (name === './ExplorerRelationshipsView') return { default: 'RelationshipsView', ExplorerLinkedProperties: 'PropertiesView' };
    if (name === './ExplorerRecordLink') return { default: 'RecordLink', ExplorerRecordLink: 'RecordLink' };
    if (name === './ExplorerValue') return { ExplorerValueProvider: 'ValueProvider' };
    if (name === './SemanticTypeGlyph') return { default: 'SemanticGlyph' };
    if (name === '../../services/api' || name === '../../services/explorerProperties' || name === '../../services/publishedExplorerDetails') return tracked;
    if (name === '../../services/ontology') return { ...tracked, ontologyError: error => error.message };
    if (name === '../../services/explorerLayouts') return { ...tracked, layoutPath: (...args) => args.join('/'), publishedLayoutPath: id => '/published/' + id };
    if (name === '../../services/publishedExplorerPresentation') return { publishedPresentation: () => published, publishedColumnTypes: () => ({ caption: 'string', secret: 'string' }) };
    throw new Error('Unexpected dependency ' + name);
  };
  const component = compile(kind === 'backed' ? 'ExplorerRowLinks' : 'PublishedExplorerRelationships', require, kind === 'backed' ? 'LinkPage' : 'RelationshipPage').component;
  const render = () => { cursor = 0; scheduled.length = 0; tree = component(props); for (const [i, effect, deps] of scheduled) { effects[i]?.cleanup?.(); effects[i] = { deps, cleanup: effect() }; } return tree; };
  render();
  return { props, calls, errors, session, render, tree: () => tree,
    async flush() { for (let i = 0; i < 12; i++) { await Promise.resolve(); render(); } return tree; },
    unmount: () => effects.forEach(effect => effect?.cleanup?.()) };
}

test('Storage links use their own physical title, type and field presentation with one row read', async () => {
  const ui = mount('backed'); await ui.flush();
  assert.equal(ui.tree().type, 'RelationshipsView');
  const item = ui.tree().props.records[0];
  assert.equal(item.link.props.children, 'Physical title'); assert.equal(item.typeName, 'Physical media');
  assert.equal(item.icon.type.name, 'ExplorerTypeIcon');
  assert.deepEqual(item.link.props.identity, { id: '9007199254740993' });
  const mapping = ui.calls.find(call => call.name === 'fetchExplorerProperties');
  assert.equal(mapping.args.length, 3); assert.deepEqual(mapping.args.slice(0, 2), ['ts_ods', 'media']);
  const properties = item.properties.type(item.properties.props);
  const fields = nodes(properties).find(node => node.type === 'PropertiesView').props.fields;
  assert.deepEqual(fields.map(field => field.label), ['Physical media title']);
  ui.tree().props.onToggle('edge'); ui.render();
  item.properties.type(item.properties.props);
  assert.equal(ui.calls.filter(call => call.name === 'fetchExplorerRowByKey').length, 1);
  assert.equal(ui.calls.filter(call => call.name === 'readLayouts').length, 1);
  assert.equal(ui.tree().props.expanded, 'edge');
});

test('Published links retain native titles and metadata while using the same record header contract', async () => {
  const ui = mount('published'); await ui.flush();
  const item = ui.tree().props.records[0];
  assert.equal(item.link.props.children, 'Native title'); assert.equal(item.typeName, 'Published media');
  assert.equal(item.icon.type, 'SemanticGlyph');
  assert.deepEqual(item.link.props.identity, { $recordId: 'native-record' });
  const properties = item.properties.type(item.properties.props);
  assert.deepEqual(nodes(properties).find(node => node.type === 'PropertiesView').props.fields.map(field => field.label), ['Published media title']);
  ui.tree().props.onToggle('edge'); ui.render(); item.properties.type(item.properties.props);
  assert.equal(ui.calls.filter(call => call.name === 'fetchPublishedOntologyType').length, 1);
  assert.equal(ui.calls.filter(call => call.name === 'readLayouts').length, 1);
  assert.equal(ui.calls.filter(call => call.name === 'fetchExplorerRowByKey' || call.name === 'fetchExplorerProperties').length, 0);
});

test('related metadata failures block misleading cards and expired requests cannot populate another session', async () => {
  const failed = mount('backed', { fetchExplorerProperties: async () => { throw new Error('Denied physical profile'); } });
  assert.equal(await failed.flush(), null);
  assert.equal(failed.errors.at(-1)[0], 'Unable to read related records.');
  let finish;
  const stale = mount('backed', { fetchExplorerRowByKey: () => new Promise(resolve => { finish = resolve; }) });
  await stale.flush(); stale.session.identity = 'other-session';
  finish({ row: { caption: 'Stale' }, identity: { id: '9007199254740993' }, columnTypes: {} }); await stale.flush();
  assert.notEqual(stale.tree()?.type, 'RelationshipsView'); assert.ok(stale.errors.every(error => !error[0]));
  stale.unmount();
});
