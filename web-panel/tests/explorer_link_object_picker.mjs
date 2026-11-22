import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const jsx = (type, props) => ({ type, props });
const nodes = tree => Array.isArray(tree) ? tree.flatMap(nodes) : tree && typeof tree === 'object' ? [tree, ...[tree.props?.children].flat(Infinity).flatMap(nodes)] : [];
const compile = (file, require, suffix = '') => {
  const source = ts.transpileModule(readFileSync(new URL('../src/' + file, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const exports = {};
  vm.runInThisContext('(function(require,exports){' + source + suffix + '\n})')(require, exports);
  return exports;
};
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const physicalPage = (title = 'Company') => ({ primaryKey: ['tenant', 'id'], columns: ['tenant', 'id', 'name', 'active', 'tags'],
  columnTypes: { tenant: 'text', id: 'bigint', name: 'text', active: 'boolean', tags: 'jsonb' }, rows: [
  { tenant: 'a', id: 9007199254740992, name: title + ' A', active: true, tags: { group: 'a' } },
  { tenant: 'b', id: 9007199254740992, name: title + ' B', active: false, tags: { group: 'b' } },
], rowIdentities: [{ tenant: 'a', id: '9007199254740993' }, { tenant: 'b', id: '9007199254740995' }], hasMore: true, canPageFurther: true });
const nativePage = () => ({ count: 22, hasMore: true, items: [
  { id: 'native-a', identity: { $recordId: 'native-a' }, title: 'Native company A', properties: { id: 'physical-a', name: 'Native company A', employees: 25 } },
  { id: 'native-b', identity: { $recordId: 'native-b' }, title: 'Native company B', properties: { id: 'physical-b', name: 'Native company B', employees: 50 } },
] });
const nativeType = (mode = 'none') => ({ id: 'company-type', revision: 2, definition: {
  id: 'company-type', name: 'Native companies', source: { mode }, titleKey: 'name', primaryKey: ['id'], properties: [
    { id: 'id', name: 'Business ID', type: 'string' }, { id: 'name', name: 'Name', type: 'string' }, { id: 'employees', name: 'Employees', type: 'integer' },
  ],
} });
const physicalRelation = direction => ({ id: '18:' + direction, relationId: '18', name: 'Works for', direction,
  kind: 'logical', canLink: true, schema: 'ts_dwd', table: 'companies', fields: ['company_id'], targetFields: ['id'] });
const nativeRelation = direction => ({ id: 'relation-uuid', name: 'Works for', direction, targetDefinitionId: 'company-type' });
const normalizers = compile('services/explorerRelationshipLinks.ts', () => ({}));
const overview = compile('pages/Pipeline/ExplorerObjectOverview.tsx', name => name === 'react' ? { lazy: () => 'Preview' }
  : name === 'react/jsx-runtime' ? { jsx, jsxs: jsx } : {});
const publishedPresentation = compile('services/publishedExplorerPresentation.ts', () => ({}));

function mount({ native = false, direction = 'incoming', canLink = true, read, metadata, layouts, fresh, write = async () => {} } = {}) {
  const states = [], refs = [], effects = [], scheduled = [], reads = [], metadataReads = [], layoutReads = [], checks = [], writes = [], notifications = [], errors = [], activity = [], portals = [];
  const overlay = { host: { id: 'results-host' }, setActive: value => activity.push(value) };
  const session = { identity: 'test-account', authenticated: true, locked: false };
  let cursor = 0, tree, closed = 0, created = 0;
  const relation = native ? normalizers.publishedLinkRelation(nativeRelation(direction), canLink)
    : { ...normalizers.storageLinkRelation(physicalRelation(direction)), canLink };
  const props = { open: true, host: overlay.host, source: native ? { kind: 'published', definitionId: 'employee-type' } : { kind: 'backed', schema: 'ts_ods', table: 'employees' },
    sourceRow: { key: 'source-key', title: 'Reviewed source', identity: native ? { $recordId: 'source-native' } : { tenant: 'anchor', id: '9007199254740997' } },
    relation, onClose: () => closed++, onCreated: () => created++ };
  const available = native ? { canManage: true, relations: [nativeRelation(direction)] } : { canManage: true, relations: [physicalRelation(direction)] };
  const module = compile('pages/Pipeline/ExplorerLinkObjectPicker.tsx', name => {
    if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx };
    if (name === 'react-dom') return { createPortal: (node, host) => { portals.push(host); return node; } };
    if (name === 'react') return {
      useState(initial) { const i = cursor++; if (!(i in states)) states[i] = typeof initial === 'function' ? initial() : initial;
        return [states[i], next => { states[i] = typeof next === 'function' ? next(states[i]) : next; }]; },
      useRef(initial) { return refs[cursor++] ||= { current: initial }; }, useContext: () => overlay,
      useEffect(effect, deps) { const i = cursor++; if (!effects[i] || deps.some((value, j) => !Object.is(value, effects[i].deps[j]))) scheduled.push([i, effect, deps]); },
    };
    if (name === '@/components/ui') return { Button: 'Button', Input: 'Input', Drawer: 'Drawer', message: Object.fromEntries(['success', 'error'].map(kind => [kind, value => notifications.push({ kind, value })])) };
    if (name === '@/components/ui/icons') return { LinkOutlined: 'LinkIcon', SearchOutlined: 'SearchIcon' };
    if (name === '../../services/session') return { getSessionState: () => session };
    if (name === '../../services/ontology') return { ontologyError: (error, fallback) => error.message || fallback,
      fetchPublishedOntologyType: async (...args) => { metadataReads.push(args); return metadata ? metadata(...args) : nativeType(); } };
    if (name === '../../services/explorerProperties') return { fetchExplorerProperties: async (...args) => { metadataReads.push(args); return metadata ? metadata(...args) : {
      displayName: 'Companies', titleProperty: 'name', dataType: 'company', properties: [
        { id: 'name', name: 'Company name', sourceColumn: 'name' }, { id: 'active', name: 'Operating', sourceColumn: 'active' },
      ],
    }; } };
    if (name === '../../services/explorerLayouts') return {
      layoutPath: (schema, table) => '/layout/' + schema + '/' + table,
      publishedLayoutPath: definitionId => '/published-layout/' + definitionId,
      readLayouts: async (...args) => { layoutReads.push(args); return typeof layouts === 'function' ? layouts(...args) : layouts || { personal: { layout: null }, default: { layout: null } }; },
    };
    if (name === '../../services/publishedExplorerPresentation') return publishedPresentation;
    if (name === '../../services/api') return {
      queryExplorerPreview: async (...args) => { reads.push(args); return read ? read(...args) : physicalPage(); },
      fetchExplorerRowLinks: async (...args) => { checks.push(args); return fresh ? fresh(...args) : available; },
      createExplorerRowLink: async (...args) => { writes.push(args); return write(...args); },
    };
    if (name === '../../services/publishedExplorer') return { queryPublishedExplorer: async (...args) => { reads.push(args); return read ? read(...args) : nativePage(); } };
    if (name === '../../services/publishedExplorerDetails') return {
      fetchPublishedRelationships: async (...args) => { checks.push(args); return fresh ? fresh(...args) : available; },
      createPublishedRelationship: async (...args) => { writes.push(args); return write(...args); },
    };
    if (name === './ExplorerObjectOverview') return { ...overview, ExplorerTypeIcon: 'TypeIcon' };
    if (name === './SemanticTypeGlyph') return { default: 'SemanticTypeIcon' };
    if (name === './ExplorerLinkTargetTable') return { default: 'TargetTable' };
    if (name === './ExplorerResultsFooter') return { ExplorerResultsFooter: 'ResultsFooter' };
    if (name === './ExplorerSelectionOverlayContext') return { ExplorerSelectionOverlayContext: 'SelectionOverlayContext' };
    if (name === './useExplorerErrorToast') return { default: (...args) => errors.push(args) };
    if (name.endsWith('.css')) return {};
    throw new Error('Unexpected dependency ' + name);
  }, '\nexports.Picker=ObjectPicker;');
  const render = () => {
    cursor = 0; scheduled.length = 0; tree = module.Picker(props);
    for (const [i, effect, deps] of scheduled) { effects[i]?.cleanup?.(); effects[i] = { deps, cleanup: effect() }; }
    return tree;
  };
  render();
  const all = () => nodes(tree);
  const table = () => nodes(tree).find(node => node.type === 'TargetTable');
  const footer = () => nodes(tree).find(node => node.type === 'ResultsFooter');
  return { props, session, reads, checks, metadataReads, layoutReads, writes, notifications, errors, activity, portals, overlay, render, layer: () => tree, table, footer,
    closed: () => closed, created: () => created, rows: () => table()?.props.rows || [], selected: () => table()?.props.selectedKey,
    button: label => all().find(node => node.type === 'Button' && node.props.children === label),
    search: () => nodes(tree).find(node => node.type === 'Input'),
    choose(index = 0) { table().props.onSelect(table().props.rows[index].key); render(); },
    next() { footer().props.onNext(); render(); },
    previous() { footer().props.onPrevious(); render(); },
    resize(size) { footer().props.onPageSize(size); render(); },
    async flush() { for (let i = 0; i < 16; i++) { await Promise.resolve(); render(); } },
    unmount: () => effects.forEach(effect => effect?.cleanup?.()),
    wrapper: module.default,
  };
}

test('the physical picker reads only the peer type and submits canonical composite keys in both directions', async () => {
  for (const direction of ['incoming', 'outgoing']) {
    const ui = mount({ direction }); await ui.flush();
    assert.deepEqual(ui.reads[0].slice(0, 3), ['ts_dwd', 'companies', { search: '', offset: 0, limit: 10, filters: [], sorts: [], withIdentity: true }]);
    assert.equal(ui.reads[0][3], ui.metadataReads[0][2]);
    assert.deepEqual(ui.metadataReads[0].slice(0, 2), ['ts_dwd', 'companies']);
    assert.equal(ui.layer().props['aria-label'], 'Link object selection'); assert.equal(ui.rows().length, 2);
    assert.equal(ui.portals.at(-1), ui.overlay.host);
    assert.equal(ui.layoutReads[0][0], '/layout/ts_dwd/companies'); assert.equal(ui.layoutReads[0][1], ui.reads[0][3]);
    assert.deepEqual(ui.table().props.columns, physicalPage().columns);
    assert.equal(ui.rows()[0].columnTypes.id, 'bigint');
    assert.deepEqual(ui.rows()[1].record.tags, { group: 'b' });
    assert.equal(ui.rows()[1].identity.id, '9007199254740995');
    assert.equal(ui.button('Add link').props.disabled, true);
    ui.choose(1); await ui.button('Add link').props.onClick(); await ui.flush();
    assert.deepEqual(ui.checks[0].slice(0, 5), ['ts_ods', 'employees', ui.props.sourceRow.identity, '18:' + direction, 0]);
    assert.deepEqual(ui.writes[0].slice(0, 3), ['ts_ods', 'employees', { key: { tenant: 'anchor', id: '9007199254740997' }, relationId: '18', direction, targetKey: { tenant: 'b', id: '9007199254740995' } }]);
    assert.equal(ui.checks[0][5], ui.writes[0][3]); assert.equal(ui.created(), 1);
    assert.equal(ui.notifications[0].value.className, 'explorer-toast');
  }
});

test('Published native linking uses record identities rather than physical property values', async () => {
  for (const direction of ['incoming', 'outgoing']) {
    const ui = mount({ native: true, direction }); await ui.flush();
    assert.deepEqual(ui.reads[0].slice(0, 4), ['company-type', { filters: [], search: '' }, 10, 0]);
    assert.equal(ui.reads[0][4], ui.metadataReads[0][1]);
    ui.choose(1); await ui.button('Add link').props.onClick(); await ui.flush();
    assert.deepEqual(ui.checks[0].slice(0, 4), ['employee-type', 'source-native', 'relation-uuid:' + direction, 0]);
    assert.deepEqual(ui.writes[0].slice(0, 4), ['relation-uuid', direction, 'source-native', 'native-b']);
    assert.equal(ui.created(), 1);
    assert.deepEqual(ui.table().props.columns, ['id', 'name', 'employees']);
    assert.deepEqual(ui.rows()[0].columnTypes, { id: 'text', name: 'text', employees: 'bigint' });
    assert.equal(ui.layoutReads[0][0], '/published-layout/company-type');
  }
});

test('Published relationship services expose management capability and orient the actual native create request', async () => {
  const requests = [];
  const api = compile('services/publishedExplorerDetails.ts', name => name === './endpoints' ? { apiUrl: value => value } : {
    graphRequest: async (...args) => { requests.push(args); return args[1].endsWith('/link-types') ? { items: [], hasMore: false, canManage: true } : {}; },
  });
  const signal = new AbortController().signal;
  assert.equal((await api.fetchPublishedRelationships('employees', 'record-a', undefined, 0, signal)).canManage, true);
  for (const direction of ['incoming', 'outgoing']) {
    await api.createPublishedRelationship('link/type', direction, 'record-a', 'record-b', signal);
    assert.equal(requests.at(-1)[1], '/link-types/link%2Ftype/links');
    assert.equal(requests.at(-1)[2].signal, signal);
    assert.deepEqual(requests.at(-1)[2].data, direction === 'outgoing' ? { sourceRecordId: 'record-a', targetRecordId: 'record-b' } : { sourceRecordId: 'record-b', targetRecordId: 'record-a' });
  }
});

test('target tables preserve personal column layout, names and widths while appending new fields', async () => {
  const ui = mount({ layouts: {
    personal: { layout: { columns: [{ field: 'name', visible: true, width: 420 }, { field: 'tenant', visible: false, width: 110 }] } },
    default: { layout: { columns: [{ field: 'id', visible: true, width: 140 }, { field: 'name', visible: false, width: 180 }] } },
  } }); await ui.flush();
  assert.deepEqual(ui.table().props.columns, ['name', 'id', 'active', 'tags']);
  assert.equal(ui.table().props.widths.name, 420);
  assert.equal(ui.table().props.names.name, 'Company name'); assert.equal(ui.table().props.names.active, 'Operating');
  assert.deepEqual(ui.rows()[0].record, physicalPage().rows[0]);
  const fromDefault = mount({ layouts: { personal: { layout: null }, default: { layout: {
    columns: [{ field: 'name', visible: true, width: 360 }, { field: 'id', visible: false, width: 100 }],
  } } } }); await fromDefault.flush();
  assert.deepEqual(fromDefault.table().props.columns, ['name', 'tenant', 'active', 'tags']);
  assert.equal(fromDefault.table().props.widths.name, 360);
});

test('the shared pagination footer changes between 10, 20 and 50 rows and clears the reviewed target', async () => {
  for (const native of [false, true]) {
    const ui = mount({ native }); await ui.flush();
    assert.equal(ui.footer().props.pageSize, 10);
    ui.choose(); ui.next(); await ui.flush(); assert.equal(ui.footer().props.offset, 10);
    ui.choose(); ui.resize(20);
    assert.equal(ui.button('Add link').props.disabled, true); await ui.flush();
    assert.equal(ui.footer().props.pageSize, 20); assert.equal(ui.footer().props.offset, 0); assert.ok(!ui.selected());
    if (native) assert.deepEqual(ui.reads.at(-1).slice(0, 4), ['company-type', { filters: [], search: '' }, 20, 0]);
    else assert.equal(ui.reads.at(-1)[2].limit, 20);
    ui.next(); await ui.flush(); assert.equal(ui.footer().props.offset, 20);
    ui.choose(); ui.resize(50); await ui.flush();
    assert.equal(ui.footer().props.pageSize, 50); assert.equal(ui.footer().props.offset, 0); assert.ok(!ui.selected());
    if (native) assert.equal(ui.reads.at(-1)[2], 50); else assert.equal(ui.reads.at(-1)[2].limit, 50);
    ui.next(); await ui.flush(); assert.equal(ui.footer().props.offset, 50);
  }
});

test('a missing saved layout fails explicitly through Toast and never presents a substitute table', async () => {
  let failed = true;
  const ui = mount({ layouts: async () => { if (failed) throw new Error('Saved layout unavailable'); return { personal: { layout: null }, default: { layout: null } }; } });
  await ui.flush(); assert.equal(ui.rows().length, 0); assert.equal(ui.button('Add link').props.disabled, true);
  const failure = ui.errors.findLast(([error]) => error); assert.equal(failure[0], 'Saved layout unavailable');
  failed = false; failure[2](); ui.render(); await ui.flush(); assert.equal(ui.rows().length, 2);
});

test('a pending request locks duplicate adds, cancellation, paging and target changes', async () => {
  const pending = deferred(), ui = mount({ write: () => pending.promise }); await ui.flush(); ui.choose();
  const adding = ui.button('Add link').props.onClick(); await ui.flush();
  assert.equal(ui.button('Cancel').props.disabled, true); assert.equal(ui.table().props.busy, true); assert.equal(ui.footer().props.locked, true);
  await ui.button('Add link').props.onClick(); ui.button('Cancel').props.onClick();
  ui.next(); ui.resize(50); ui.choose(1); await ui.flush();
  assert.equal(ui.writes.length, 1); assert.equal(ui.checks.length, 1); assert.equal(ui.closed(), 0); assert.equal(ui.reads.length, 1);
  assert.equal(ui.selected(), ui.rows()[0].key);
  pending.resolve(); await adding; await ui.flush();
  assert.equal(ui.created(), 1); assert.equal(ui.notifications.length, 1);
});

test('pagination clears the target, blocks queued navigation and refreshes selection after changing page size', async () => {
  const old = deferred(); let requests = 0;
  const ui = mount({ read: async () => ++requests === 2 ? old.promise : physicalPage(requests === 1 ? 'Initial' : 'Newest') });
  await ui.flush(); ui.choose();
  ui.next();
  await ui.button('Add link').props.onClick(); assert.equal(ui.writes.length, 0);
  ui.resize(20); ui.next(); await ui.flush(); assert.equal(ui.reads.length, 2);
  old.resolve(physicalPage('Second page')); await ui.flush();
  assert.equal(ui.rows()[0].title, 'Second page A');
  ui.choose(); ui.resize(20); assert.equal(ui.button('Add link').props.disabled, true); await ui.flush();
  assert.equal(ui.rows()[0].title, 'Newest A');
  assert.ok(!ui.selected());
  assert.deepEqual(ui.reads[2][2], { search: '', offset: 0, limit: 20, filters: [], sorts: [], withIdentity: true });
  ui.choose(); ui.next();
  await ui.button('Add link').props.onClick(); await ui.flush();
  assert.equal(ui.reads[3][2].offset, 20); assert.ok(!ui.selected());
});

test('the local layer activates only its host and has no independent search, dialog or toolbar', async () => {
  const ui = mount(); await ui.flush();
  assert.deepEqual(ui.activity, [true]);
  assert.equal(ui.search(), undefined);
  assert.ok(nodes(ui.layer()).every(node => node.type !== 'Drawer' && node.type !== 'Modal'));
  ui.button('Cancel').props.onClick(); ui.unmount();
  assert.deepEqual(ui.activity, [true, false]);
  assert.equal(ui.closed(), 1);
});

test('Retry clears old rows and ignores a late earlier request', async () => {
  const pending = deferred(); let count = 0;
  const ui = mount({ read: async () => {
    if (++count === 1) throw new Error('Initial read failed');
    return count === 2 ? pending.promise : physicalPage('Fresh');
  } }); await ui.flush();
  const retry = ui.errors.findLast(([error]) => error)[2];
  retry(); ui.render(); await ui.flush();
  assert.equal(ui.rows().length, 0); assert.equal(ui.button('Add link').props.disabled, true);
  retry(); ui.render(); await ui.flush();
  assert.equal(ui.reads[1][3].aborted, true);
  pending.resolve(physicalPage('Old')); await ui.flush();
  assert.equal(ui.rows()[0].title, 'Fresh A');
  assert.ok(!ui.selected());
});

test('physical objects with absent, rounded or extra primary-key fields fail explicitly', async () => {
  const initial = physicalPage();
  for (const result of [
    { ...initial, primaryKey: [] }, { ...initial, rowIdentities: undefined },
    { ...initial, rowIdentities: [{ tenant: 'a', id: 9007199254740992 }, initial.rowIdentities[1]] },
    { ...initial, rowIdentities: [{ id: '9007199254740993' }, initial.rowIdentities[1]] },
    { ...initial, rowIdentities: [{ ...initial.rowIdentities[0], unexpected: 'field' }, initial.rowIdentities[1]] },
  ]) {
    const ui = mount({ read: async () => result }); await ui.flush();
    assert.equal(ui.rows().length, 0); assert.equal(ui.button('Add link').props.disabled, true);
    assert.ok(ui.errors.some(([error]) => error?.includes('stable primary key')));
    await ui.button('Add link').props.onClick(); assert.equal(ui.writes.length, 0);
  }
});

test('Published mapped types or inconsistent record identities never become selectable targets', async () => {
  for (const options of [
    { metadata: async () => nativeType('existing') },
    { read: async () => ({ ...nativePage(), items: [{ id: 'native-a', title: 'Mismatch', identity: { $recordId: 'other-native' } }] }) },
    { read: async () => ({ ...nativePage(), items: [{ id: 'native-a', title: 'Physical key', identity: { id: 'physical-id' } }] }) },
    { read: async () => ({ ...nativePage(), items: [{ id: null, title: 'No record', identity: { $recordId: 'native-a' } }] }) },
  ]) {
    const ui = mount({ native: true, ...options }); await ui.flush();
    assert.equal(ui.rows().length, 0); assert.equal(ui.button('Add link').props.disabled, true);
    assert.ok(ui.errors.some(([error]) => error));
    await ui.button('Add link').props.onClick(); assert.equal(ui.writes.length, 0);
  }
});

test('Published mapped types and inconsistent native record identities cannot be linked', async () => {
  for (const variant of ['mapped', 'missing', 'wrong-key', 'mismatch']) {
    const ui = mount({ native: true,
      metadata: async () => nativeType(variant === 'mapped' ? 'existing' : 'none'),
      read: async () => ({ ...nativePage(), items: [{ ...nativePage().items[0],
        ...(variant === 'missing' ? { id: undefined } : variant === 'wrong-key' ? { identity: { id: 'physical-a' } }
          : variant === 'mismatch' ? { identity: { $recordId: 'another-native' } } : {}),
      }] }),
    }); await ui.flush();
    assert.equal(ui.rows().length, 0); assert.equal(ui.button('Add link').props.disabled, true);
    assert.ok(ui.errors.some(([error]) => !!error)); assert.equal(ui.writes.length, 0);
  }
});

test('fresh source permissions, relation direction and peer endpoints are reviewed before any write', async () => {
  for (const native of [false, true]) {
    for (const changed of ['permission', 'removed', 'endpoint', 'direction']) {
      const relation = native ? nativeRelation('incoming') : physicalRelation('incoming');
      const ui = mount({ native, fresh: async () => ({ canManage: changed !== 'permission', relations: changed === 'removed' ? [] : [{ ...relation,
        ...(changed === 'direction' ? { direction: 'outgoing' } : changed === 'endpoint' ? native ? { targetDefinitionId: 'different' } : { table: 'different' } : {}),
      }] }) }); await ui.flush(); ui.choose(); await ui.button('Add link').props.onClick(); await ui.flush();
      assert.equal(ui.writes.length, 0); assert.equal(ui.created(), 0); assert.equal(ui.selected(), ui.rows()[0].key);
      assert.equal(ui.notifications[0].kind, 'error'); assert.equal(ui.notifications[0].value.className, 'explorer-toast');
    }
  }
});

test('duplicate and cardinality denials retain the reviewed target and use the shared Toast', async () => {
  for (const description of ['Objects already linked', 'Declared relationship cardinality exceeded']) {
    const ui = mount({ write: async () => { throw new Error(description); } }); await ui.flush(); ui.choose(1);
    await ui.button('Add link').props.onClick(); await ui.flush();
    assert.equal(ui.created(), 0); assert.equal(ui.closed(), 0); assert.equal(ui.selected(), ui.rows()[1].key);
    assert.equal(ui.notifications.length, 1); assert.equal(ui.notifications[0].kind, 'error');
    assert.equal(ui.notifications[0].value.description, description); assert.equal(ui.button('Add link').props.disabled, false);
  }
});

test('read failures can be retried from Toast, while empty and bounded results disable the shared paging actions', async () => {
  let count = 0;
  const ui = mount({ read: async () => { if (++count === 1) throw new Error('Object read denied'); return physicalPage(); } }); await ui.flush();
  const error = ui.errors.findLast(([error]) => error); assert.equal(error[0], 'Object read denied');
  error[2](); ui.render(); await ui.flush(); assert.equal(ui.rows().length, 2);
  for (const limited of [false, true]) {
    const result = limited ? { ...physicalPage(), canPageFurther: false } : { ...physicalPage(), rows: [], rowIdentities: [], hasMore: false };
    const ui = mount({ read: async () => result }); await ui.flush();
    assert.equal(ui.footer().props.canPageFurther, false); assert.equal(ui.footer().props.offset, 0);
    if (limited) assert.equal(ui.footer().props.notices, 'Object preview limit reached.');
    else assert.equal(ui.button('Add link').props.disabled, true);
  }
});

test('unselected, read-only, closed and cancelled pickers never create links', async () => {
  for (const canLink of [true, false]) {
    const ui = mount({ canLink }); await ui.flush(); await ui.button('Add link').props.onClick();
    if (!canLink) { ui.choose(); await ui.button('Add link').props.onClick(); }
    ui.button('Cancel').props.onClick(); assert.equal(ui.closed(), 1); assert.equal(ui.writes.length, 0); assert.equal(ui.checks.length, 0);
    assert.equal(ui.wrapper({ ...ui.props, open: false }), null);
  }
});

test('late reads, permission checks and writes cannot cross a locked session or unmounted dialog', async () => {
  const pendingRead = deferred(), reading = mount({ read: () => pendingRead.promise });
  reading.session.identity = 'another-account'; pendingRead.resolve(physicalPage()); await reading.flush();
  assert.equal(reading.rows().length, 0); assert.equal(reading.notifications.length, 0);
  for (const native of [false, true]) for (const leave of ['lock', 'session', 'unmount']) {
    const permission = deferred(), checking = mount({ native, fresh: () => permission.promise }); await checking.flush(); checking.choose();
    const beforeWrite = checking.button('Add link').props.onClick(); await checking.flush();
    await checking.button('Add link').props.onClick(); assert.equal(checking.checks.length, 1);
    if (leave === 'unmount') { checking.unmount(); assert.equal(checking.checks[0][native ? 4 : 5].aborted, true); }
    else if (leave === 'lock') checking.session.locked = true;
    else checking.session.identity = 'another-account';
    permission.resolve({ canManage: true, relations: [native ? nativeRelation('incoming') : physicalRelation('incoming')] }); await beforeWrite;
    assert.equal(checking.writes.length, 0); assert.equal(checking.created(), 0); assert.equal(checking.notifications.length, 0);
  }
  for (const leave of ['lock', 'unmount']) {
    const pending = deferred(), ui = mount({ write: () => pending.promise }); await ui.flush(); ui.choose();
    const adding = ui.button('Add link').props.onClick(); await ui.flush();
    if (leave === 'lock') ui.session.locked = true; else ui.unmount();
    if (leave === 'unmount') assert.equal(ui.writes[0][3].aborted, true);
    pending.resolve(); await adding; await ui.flush();
    assert.equal(ui.created(), 0); assert.equal(ui.notifications.length, 0);
  }
});
