import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const jsx = (type, props) => ({ type, props });
const nodes = tree => tree && typeof tree === 'object' ? [tree, ...[tree.props?.children].flat(Infinity).flatMap(nodes)] : [];
const text = value => Array.isArray(value) ? value.map(text).join('') : value?.props ? text(value.props.children) : value == null || value === false ? '' : String(value);
const types = [
  ['mysql', 'MySQL', 3306, 'relational'], ['postgresql', 'PostgreSQL', 5432, 'relational'],
  ['s3', 'S3-compatible storage', 443, 'object_storage'], ['redis', 'Redis', 6379, 'non_relational'],
  ['clickhouse', 'ClickHouse', 8443, 'relational'], ['mongodb', 'MongoDB', 27017, 'non_relational'],
].map(([engine, name, port, category]) => ({ engine, name, port, category, driverAvailable: true, catalogAvailable: false }));
const source = (engine = 'mysql', patch = {}) => ({ id: 'source-a', name: 'Synthetic source', engine,
  config: { host: 'db.synthetic.test', port: types.find(type => type.engine === engine).port, database: 'analytics', username: 'reader', auth: 'password', tls: true,
    ...(engine === 'mongodb' ? { authDatabase: 'admin' } : {}) },
  credentialsConfigured: true, catalogAvailable: false, updatedAt: '2026-01-01T00:00:00Z', testedAt: null, ...patch });
let instance = 0;

async function mount({ sources = [], selected = '', overrides = {} } = {}) {
  let cursor = 0, tree, params = new URLSearchParams(selected ? { sourceId: selected } : {}), mounted = true;
  const state = [], refs = [], effects = [], scheduled = [], notices = [], destroyed = [], errorHooks = [], requests = [];
  const session = { identity: 'synthetic-account', authenticated: true, locked: false }, id = 'data-connections-' + ++instance;
  const store = { sources: [...sources] };
  const defaults = {
    fetchCurrentUserContext: async () => ({ user: { is_superuser: true } }),
    fetchDataConnectorTypes: async () => types,
    fetchDataSources: async () => [...store.sources],
    testDataSource: async () => ({ ok: true, message: 'Connected to synthetic source.', latencyMs: 17 }),
    createDataSource: async payload => { const saved = source(payload.engine, { name: payload.name, config: payload.config }); store.sources.push(saved); return saved; },
    updateDataSource: async (identifier, payload) => { const saved = source(payload.engine, { id: identifier, name: payload.name, config: payload.config }); store.sources = store.sources.map(row => row.id === identifier ? saved : row); return saved; },
    deleteDataSource: async identifier => { store.sources = store.sources.filter(row => row.id !== identifier); },
  };
  const implementations = { ...defaults, ...overrides };
  const api = Object.fromEntries(Object.keys(defaults).map(name => [name, (...args) => { requests.push({ name, args }); return implementations[name](...args); }]));
  const message = Object.fromEntries(['success', 'error', 'loading'].map(kind => [kind, value => {
    const notice = { kind, value, closed: false }; notices.push(notice); return () => { notice.closed = true; };
  }]));
  message.destroy = key => { destroyed.push(key); notices.filter(notice => notice.value.key === key).forEach(notice => { notice.closed = true; }); };
  const react = {
    useState(initial) { const i = cursor++; if (!(i in state)) state[i] = typeof initial === 'function' ? initial() : initial; return [state[i], next => { state[i] = typeof next === 'function' ? next(state[i]) : next; }]; },
    useRef(initial) { return refs[cursor++] ||= { current: initial }; },
    useId() { cursor++; return id; },
    useEffect(effect, deps) { const i = cursor++; if (!effects[i] || deps.some((value, j) => !Object.is(value, effects[i].deps[j]))) scheduled.push([i, effect, deps]); },
  };
  const require = name => {
    if (name === 'react') return react;
    if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx, Fragment: 'Fragment' };
    if (name === 'react-router-dom') return { Link: 'Link', useSearchParams: () => [params, next => { params = typeof next === 'function' ? next(params) : next; }] };
    if (name === '@/components/ui') return { Button: 'Button', Checkbox: 'Checkbox', Input: 'Input', Modal: 'Modal', Select: 'Select', NativeSelect: 'NativeSelect', message };
    if (name === '@/components/ui/icons') return new Proxy({}, { get: (_, key) => String(key) });
    if (name === '../../services/api') return api;
    if (name === '../../services/session') return { getSessionState: () => session };
    if (name === './useExplorerErrorToast') return { default: (...args) => errorHooks.push(args) };
    if (name.endsWith('.css')) return {};
    if (name.startsWith('./')) return { default: name.slice(2) };
    throw new Error('Unexpected dependency ' + name);
  };
  const code = ts.transpileModule(readFileSync(new URL('../src/pages/Pipeline/DataConnections.tsx', import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const exports = {};
  vm.runInThisContext('(function(require,exports,window){' + code + '\n})')(require, exports, { addEventListener() {}, removeEventListener() {} });
  const render = () => {
    if (!mounted) return tree;
    cursor = 0; scheduled.length = 0; errorHooks.length = 0; tree = exports.default();
    for (const [i, effect, deps] of scheduled) { effects[i]?.cleanup?.(); effects[i] = { deps, cleanup: effect() }; }
    return tree;
  };
  const ui = {
    session, store, implementations, requests, notices, destroyed, errorHooks, render,
    nodes: () => nodes(tree),
    feedback: () => notices.filter(notice => notice.kind !== 'loading'),
    calls: name => requests.filter(request => request.name === name),
    button: name => nodes(tree).find(node => node.type === 'Button' && text(node.props.children) === name),
    field: name => nodes(tree).find(node => node.type === 'Input' && node.props['aria-label'] === name),
    confirmation: () => nodes(tree).find(node => node.type === 'Modal' && node.props.okText),
    wizard: () => nodes(tree).find(node => node.type === 'Modal' && node.props.className.includes('ontology-connectors__wizard')),
    click(name) { const button = this.button(name); assert.ok(button, 'Missing button: ' + name); assert.ok(!button.props.disabled, 'Disabled button: ' + name); const result = button.props.onClick(); render(); return result; },
    fill(name, value) { const field = this.field(name); assert.ok(field, 'Missing input: ' + name); field.props.onChange({ target: { value } }); render(); },
    select(name, value) {
      const field = nodes(tree).find(node => ['Select', 'NativeSelect'].includes(node.type) && node.props['aria-label'] === name);
      assert.ok(field, 'Missing select: ' + name);
      field.props.onChange(field.type === 'Select' ? value : { target: { value } }); render();
    },
    async settle() { for (let i = 0; i < 4; i++) { await new Promise(resolve => setImmediate(resolve)); render(); } },
    choose(engine = 'mysql', category) { this.click('New source'); if (category) this.select('Filter connector category', category); const card = nodes(tree).find(node => node.type === 'button' && node.props['aria-label'] === types.find(type => type.engine === engine).name); card.props.onClick(); render(); this.click('Continue'); },
    valid() { for (const [name, value] of Object.entries({ 'Connection name': 'Synthetic source', Host: 'db.synthetic.test', Database: 'analytics', Username: 'reader', Password: 'synthetic-password' })) this.fill(name, value); },
    noInlineErrors() { assert.equal(nodes(tree).filter(node => node.props?.role === 'alert').length, 0); },
    unmount() { mounted = false; effects.forEach(effect => effect?.cleanup?.()); },
  };
  render(); await ui.settle(); return ui;
}

test('repeated invalid connection submissions each show a toast without calling a probe or adding an error row', async () => {
  const ui = await mount(); ui.choose();
  ui.click('Test connection'); ui.click('Test connection'); ui.click('Save source');
  assert.equal(ui.feedback().length, 3);
  for (const notice of ui.feedback()) {
    assert.equal(notice.kind, 'error'); assert.equal(notice.value.className, 'explorer-toast');
    assert.equal(notice.value.content, 'Enter a connection name, host and port between 1 and 65535.');
    assert.equal(notice.value.key, ui.feedback()[0].value.key);
  }
  assert.equal(ui.calls('testDataSource').length, 0); assert.equal(ui.calls('createDataSource').length, 0);
  ui.noInlineErrors(); assert.ok(ui.wizard()); ui.unmount();
});

test('connection tests report successful probes, rejected probes and request failures through toast only', async () => {
  const ui = await mount(); ui.choose(); ui.valid();
  ui.click('Test connection'); await ui.settle();
  assert.equal(ui.feedback().at(-1).kind, 'success'); assert.equal(ui.feedback().at(-1).value.description, '17 ms');
  ui.implementations.testDataSource = async () => ({ ok: false, message: 'Read access rejected.', latencyMs: 9 });
  ui.click('Test connection'); await ui.settle();
  assert.equal(ui.feedback().at(-1).kind, 'error'); assert.equal(ui.feedback().at(-1).value.content, 'Read access rejected.');
  ui.implementations.testDataSource = async () => { throw new Error('Probe service unavailable.'); };
  ui.click('Test connection'); await ui.settle();
  assert.equal(ui.feedback().at(-1).value.content, 'Probe service unavailable.');
  assert.equal(ui.calls('testDataSource').length, 3); ui.noInlineErrors(); ui.unmount();
});

test('save and confirmed delete notify through toast, and a failed delete keeps the confirmation available for retry', async () => {
  const ui = await mount(); ui.choose(); ui.valid();
  ui.click('Save source'); await ui.settle();
  assert.equal(ui.calls('createDataSource').length, 1); assert.equal(ui.wizard(), undefined);
  assert.equal(ui.feedback().at(-1).kind, 'success'); assert.match(ui.feedback().at(-1).value.content, /Source saved/);
  ui.click('Delete source'); assert.equal(ui.calls('deleteDataSource').length, 0);
  ui.implementations.deleteDataSource = async () => { throw new Error('Deletion rejected.'); };
  await ui.confirmation().props.onOk(); await ui.settle();
  assert.ok(ui.confirmation()); assert.equal(ui.feedback().at(-1).value.content, 'Deletion rejected.'); ui.noInlineErrors();
  ui.implementations.deleteDataSource = async identifier => { ui.store.sources = ui.store.sources.filter(row => row.id !== identifier); };
  await ui.confirmation().props.onOk(); await ui.settle();
  assert.equal(ui.confirmation(), undefined); assert.equal(ui.calls('deleteDataSource').length, 2);
  assert.equal(ui.feedback().at(-1).kind, 'success'); assert.equal(ui.feedback().at(-1).value.content, 'Data source deleted.'); ui.unmount();
});

test('a PostgreSQL revision conflict clears the entered password and locks saving until the saved source is reloaded', async () => {
  const saved = source('postgresql');
  const ui = await mount({ sources: [saved], selected: saved.id,
    overrides: { updateDataSource: async () => { throw { response: { status: 409, data: { detail: { code: 'source_revision_changed' } } } }; } } });
  ui.click('Edit connection'); ui.fill('Password', 'replacement-secret'); ui.click('Save source'); await ui.settle();
  assert.equal(ui.field('Password').props.value, ''); assert.equal(ui.button('Save source').props.disabled, true);
  assert.equal(ui.nodes().find(node => node.type === 'fieldset').props.disabled, true);
  assert.match(ui.feedback().at(-1).value.content, /password was cleared/);
  assert.equal(ui.calls('updateDataSource')[0].args[1].expectedUpdatedAt, saved.updatedAt);
  ui.noInlineErrors(); ui.click('Reload saved source'); await ui.settle();
  assert.equal(ui.wizard(), undefined); assert.ok(ui.calls('fetchDataSources').length >= 2);
  ui.click('Edit connection'); assert.ok(ui.button('Test connection'));
  assert.equal(ui.nodes().find(node => node.type === 'fieldset').props.disabled, false); ui.unmount();
});

test('locked sessions and unmounted pages suppress late probe notifications and clean only their own toast key', async () => {
  for (const mode of ['locked', 'unmounted']) {
    let resolve, reject;
    const ui = await mount({ overrides: { testDataSource: () => new Promise((done, fail) => { resolve = done; reject = fail; }) } });
    ui.choose(); ui.valid(); ui.click('Test connection');
    if (mode === 'locked') { ui.session.locked = true; reject(new Error('Late private failure.')); }
    else { ui.unmount(); resolve({ ok: true, message: 'Late private result.', latencyMs: 2 }); }
    await ui.settle(); assert.equal(ui.feedback().length, 0);
    assert.ok(ui.destroyed.length > 0); assert.ok(ui.destroyed.every(key => typeof key === 'string'));
    ui.unmount();
  }
});

test('registry loading errors retain blocked controls and toast retry, without an inline API alert', async () => {
  let failing = true;
  const ui = await mount({ overrides: { fetchDataSources: async () => { if (failing) throw new Error('Registry unavailable.'); return []; } } });
  const [failure, title, retry] = ui.errorHooks.find(([, title]) => title === 'Unable to load data sources');
  assert.equal(failure, 'Registry unavailable.'); assert.equal(title, 'Unable to load data sources');
  assert.equal(ui.button('New source').props.disabled, true); ui.noInlineErrors();
  failing = false; retry(); await ui.settle();
  assert.equal(ui.calls('fetchDataSources').length, 2); assert.equal(ui.button('New source').props.disabled, false);
  assert.equal(ui.errorHooks.find(([, title]) => title === 'Unable to load data sources')[0], ''); ui.unmount();
});

test('hosting providers pair every name with its icon in the picker, saved details and editor', async () => {
  const labels = {
    self_hosted: 'Self-hosted', aws: 'Amazon Web Services', azure: 'Microsoft Azure', gcp: 'Google Cloud',
    aliyun: 'Alibaba Cloud', tencent: 'Tencent Cloud', huawei: 'Huawei Cloud', oracle_cloud: 'Oracle Cloud', other: 'Other cloud provider',
  };
  const assertLabel = (label, provider) => {
    assert.equal(text(label), labels[provider]);
    assert.deepEqual(nodes(label).filter(n => n.type === 'HostingProviderIcon').map(n => n.props.provider), [provider]);
  };
  const ui = await mount(); ui.choose();
  const picker = () => ui.nodes().find(n => n.type === 'Select' && n.props['aria-label'] === 'Hosting provider').props;
  assert.deepEqual(picker().options.map(option => option.value).sort(), Object.keys(labels).sort());
  for (const option of picker().options) assertLabel(option.label, option.value);
  ui.valid(); ui.select('Hosting provider', 'tencent');
  ui.click('Save source'); await ui.settle();
  const detail = ui.nodes().find(n => n.type === 'dd' && text(n.props.children) === labels.tencent);
  assert.ok(detail); assertLabel(detail, 'tencent');
  ui.click('Edit connection');
  const { options, value } = picker(); assert.equal(value, 'tencent');
  assertLabel(options.find(option => option.value === value).label, 'tencent');
  ui.unmount();
});

test('S3 bucket, signing region and provider survive saving and editing while the secret stays write-only', async () => {
  const ui = await mount(); ui.choose('s3', 'object_storage');
  assert.equal(ui.field('Port').props.value, '443'); assert.equal(ui.field('Database'), undefined);
  ui.fill('Connection name', 'Reports bucket'); ui.fill('Host', 's3.us-east-1.amazonaws.com');
  ui.select('Hosting provider', 'aws'); ui.fill('Access key ID', 'synthetic-reader'); ui.fill('Secret access key', 'synthetic-secret');
  ui.click('Test connection'); assert.equal(ui.calls('testDataSource').length, 0);
  ui.fill('Bucket', 'reports'); ui.fill('Signing region', 'us-east-1');
  assert.equal(ui.nodes().find(node => node.props['aria-label'] === 'Bucket addressing').props.value, 'path');
  ui.select('Bucket addressing', 'virtual');
  ui.click('Save source'); await ui.settle();
  const { config, credentials } = ui.calls('createDataSource')[0].args[0];
  assert.deepEqual(config, { host: 's3.us-east-1.amazonaws.com', port: 443, username: 'synthetic-reader', auth: 'access_key', database: '', tls: true, bucket: 'reports', region: 'us-east-1', provider: 'aws', addressingStyle: 'virtual' });
  assert.deepEqual(credentials, { secretAccessKey: 'synthetic-secret' });
  const summary = ui.nodes().filter(n => n.type === 'dd').map(n => text(n.props.children));
  for (const expected of ['Amazon Web Services', 'reports', 'us-east-1', 'Virtual-hosted style']) assert.ok(summary.includes(expected));
  ui.click('Edit connection'); assert.equal(ui.field('Secret access key').props.value, '');
  assert.equal(ui.nodes().find(node => node.props['aria-label'] === 'Bucket addressing').props.value, 'virtual');
  assert.equal(ui.field('Bucket').props.value, 'reports'); assert.equal(ui.field('Signing region').props.value, 'us-east-1');
  ui.fill('Connection name', 'Renamed bucket'); ui.click('Test connection'); await ui.settle();
  assert.deepEqual(ui.calls('testDataSource')[0].args[0].credentials, {});
  ui.fill('Bucket', 'different-bucket'); ui.click('Test connection'); assert.equal(ui.calls('testDataSource').length, 1);
  ui.unmount();
});

test('Redis validates the database index and ClickHouse starts with its HTTP TLS port and default database', async () => {
  for (const engine of ['redis', 'clickhouse']) {
    const ui = await mount(); ui.choose(engine);
    ui.select('Hosting provider', 'aliyun'); ui.fill('Connection name', 'Hosted source'); ui.fill('Host', 'cloud.synthetic.test'); ui.fill('Password', 'synthetic-password');
    if (engine === 'redis') {
      assert.equal(ui.field('Database index').props.value, '0'); assert.equal(ui.field('Username').props.value, 'default');
      for (const invalid of ['-1', '01', '1.5']) {
        ui.fill('Database index', invalid); ui.click('Test connection'); assert.equal(ui.calls('testDataSource').length, 0);
      }
      ui.fill('Database index', '2');
    } else { assert.equal(ui.field('Database').props.value, 'default'); ui.fill('Username', 'reader'); }
    ui.click('Test connection'); await ui.settle();
    const { config } = ui.calls('testDataSource')[0].args[0];
    assert.equal(config.port, engine === 'redis' ? 6379 : 8443); assert.equal(config.provider, 'aliyun'); assert.equal(config.tls, true); ui.unmount();
  }
});

test('editing an existing database shows self-hosted and changing providers requires fresh credentials', async () => {
  const saved = source(); const ui = await mount({ sources: [saved], selected: saved.id });
  ui.click('Edit connection');
  const provider = ui.nodes().find(node => node.props['aria-label'] === 'Hosting provider');
  assert.equal(provider.props.value, 'self_hosted');
  ui.select('Hosting provider', 'azure'); ui.click('Save source'); assert.equal(ui.calls('updateDataSource').length, 0);
  ui.select('Hosting provider', 'self_hosted'); ui.fill('Connection name', 'Renamed'); ui.click('Save source'); await ui.settle();
  const payload = ui.calls('updateDataSource')[0].args[1];
  assert.equal(payload.config.provider, undefined); assert.deepEqual(payload.credentials, {}); ui.unmount();
});

test('MongoDB requires a target database and tests and saves its own authentication database', async () => {
  const ui = await mount(); ui.choose('mongodb', 'non_relational');
  assert.equal(ui.field('Port').props.value, '27017');
  assert.equal(ui.field('Authentication database').props.value, 'admin');
  ui.valid(); ui.fill('Database', '');
  ui.click('Test connection');
  assert.equal(ui.calls('testDataSource').length, 0);
  assert.match(ui.feedback().at(-1).value.content, /database or service name/);

  ui.fill('Database', 'documents');
  ui.fill('Authentication database', '');
  ui.click('Test connection'); await ui.settle();
  const tested = ui.calls('testDataSource')[0].args[0];
  assert.equal(tested.engine, 'mongodb');
  assert.equal(tested.config.database, 'documents');
  assert.equal(tested.config.authDatabase, 'admin');
  assert.equal(tested.config.port, 27017);
  assert.equal(tested.config.tls, true);

  ui.fill('Authentication database', ' accounts ');
  ui.click('Save source'); await ui.settle();
  assert.equal(ui.calls('createDataSource')[0].args[0].config.authDatabase, 'accounts');
  ui.unmount();
});

test('changing the MongoDB authentication database requires re-entering credentials', async () => {
  const saved = source('mongodb');
  const ui = await mount({ sources: [saved], selected: saved.id });
  ui.click('Edit connection');
  assert.equal(ui.field('Authentication database').props.value, 'admin');
  ui.fill('Authentication database', 'accounts');
  ui.click('Test connection');
  assert.equal(ui.calls('testDataSource').length, 0);
  assert.match(ui.feedback().at(-1).value.content, /Enter credentials again/);
  ui.fill('Authentication database', '');
  ui.click('Test connection'); await ui.settle();
  assert.equal(ui.calls('testDataSource')[0].args[0].config.authDatabase, 'admin');
  assert.deepEqual(ui.calls('testDataSource')[0].args[0].credentials, {});
  ui.unmount();
});

test('MySQL and MongoDB use distinct vector marks rather than database initials', () => {
  const code = ts.transpileModule(readFileSync(new URL('../src/pages/Pipeline/ConnectorMark.tsx', import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const exports = {};
  vm.runInThisContext('(function(require,exports){' + code + '\n})')(name =>
    name === 'react/jsx-runtime' ? { jsx, jsxs: jsx } : { default: 'PostgreSQL' }, exports);
  const marks = ['mysql', 'mongodb'].map(engine => {
    const mark = exports.default({ engine });
    assert.equal(mark.props['aria-hidden'], 'true');
    assert.match(mark.props.className, new RegExp('is-' + engine));
    const glyph = mark.props.children.type();
    assert.equal(glyph.type, 'svg');
    return nodes(glyph).find(node => node.type === 'path').props.d;
  });
  assert.notEqual(...marks);
});
