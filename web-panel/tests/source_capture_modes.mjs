import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const jsx = (type, props) => ({ type, props });
const nodes = tree => tree && typeof tree === 'object' ? [tree, ...[tree.props?.children].flat(Infinity).flatMap(nodes)] : [];
const text = value => Array.isArray(value) ? value.map(text).join('') : value?.props ? text(value.props.children) : value == null || value === false ? '' : String(value);
const source = engine => ({ id: 'source-id', engine, updatedAt: '2026-11-24T00:00:00Z', config: { database: 'example' } });

async function mount(component, saved, api, extraProps = {}) {
  let cursor = 0, tree;
  const state = [], effects = [], scheduled = [], calls = [];
  const react = {
    useState(initial) { const i = cursor++; if (!(i in state)) state[i] = initial; return [state[i], next => { state[i] = typeof next === 'function' ? next(state[i]) : next; }]; },
    useEffect(effect, deps) { const i = cursor++; if (!effects[i] || deps.some((value, j) => !Object.is(value, effects[i].deps[j]))) scheduled.push([i, effect, deps]); },
  };
  const require = name => {
    if (name === 'react') return react;
    if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx, Fragment: 'Fragment' };
    if (name === '@/components/ui') return { Button: 'Button', Checkbox: 'Checkbox', Input: 'Input', NativeSelect: 'NativeSelect' };
    if (name === '../../services/api') return Object.fromEntries(Object.entries(api).map(([key, value]) => [key, (...args) => { calls.push({ key, args }); return value(...args); }]));
    if (name.endsWith('.css')) return {};
    throw new Error('Unexpected dependency ' + name);
  };
  const code = ts.transpileModule(readFileSync(new URL(`../src/pages/Pipeline/${component}.tsx`, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const exports = {};
  vm.runInThisContext('(function(require,exports,window,setTimeout,clearTimeout){' + code + '\n})')(
    require, exports, { confirm: () => true }, () => 0, () => {},
  );
  const render = () => {
    cursor = 0; scheduled.length = 0; tree = exports.default({ source: saved, ...extraProps });
    for (const [i, effect, deps] of scheduled) { effects[i]?.cleanup?.(); effects[i] = { deps, cleanup: effect() }; }
  };
  const ui = {
    calls,
    nodes: () => nodes(tree), text: () => text(tree),
    button: label => nodes(tree).find(node => node.type === 'Button' && text(node.props.children) === label),
    fill(label, value) { const parent = nodes(tree).find(node => node.type === 'label' && text(node).startsWith(label)); assert.ok(parent, 'Missing field ' + label); nodes(parent).find(node => node.type === 'Input').props.onChange({ target: { value } }); render(); },
    select(label, value) { const parent = nodes(tree).find(node => node.type === 'label' && text(node).startsWith(label)); assert.ok(parent, 'Missing select ' + label); nodes(parent).find(node => node.type === 'NativeSelect').props.onChange({ target: { value } }); render(); },
    check(label, checked) { const checkbox = nodes(tree).find(node => node.type === 'Checkbox' && text(node) === label); assert.ok(checkbox, 'Missing checkbox ' + label); checkbox.props.onChange({ target: { checked } }); render(); },
    submit() { nodes(tree).find(node => node.type === 'form').props.onSubmit({ preventDefault() {} }); render(); },
    async settle() { for (let i = 0; i < 4; i++) { await new Promise(resolve => setImmediate(resolve)); render(); } },
    close() { for (const effect of effects) effect?.cleanup?.(); },
  };
  render(); await ui.settle(); return ui;
}

test('logical layer choices come from the registry and save the selected assignment', async () => {
  const saved = source('postgresql');
  const rows = [
    { layer: 'analytics', connectionId: null, name: null, executionMode: 'catalog_only' },
    { layer: 'warehouse', connectionId: saved.id, name: 'Saved source', executionMode: 'postgres_worker' },
  ];
  const ui = await mount('LayerConnectionBindingForm', saved, {
    fetchLayerBindings: async () => rows,
    bindLayerConnection: async layer => ({ ...rows[0], layer, connectionId: saved.id }),
  });
  assert.deepEqual(ui.nodes().filter(node => node.type === 'option').map(node => node.props.value), ['', 'analytics', 'warehouse']);
  assert.ok(ui.text().includes('Assigned here: warehouse'));
  ui.select('Logical layer', 'warehouse'); assert.equal(ui.button('Bind to this connection').props.disabled, true);
  ui.select('Logical layer', 'analytics'); assert.equal(ui.button('Bind to this connection').props.disabled, false);
  await ui.button('Bind to this connection').props.onClick(); await ui.settle();
  assert.deepEqual(ui.calls.find(call => call.key === 'bindLayerConnection').args, ['analytics', saved, null]);
  assert.equal(ui.button('Bind to this connection').props.disabled, true);
  ui.close();
});

test('empty and failed layer queries do not offer hardcoded layer choices', async () => {
  for (const fail of [false, true]) {
    const ui = await mount('LayerConnectionBindingForm', source('postgresql'), {
      fetchLayerBindings: async () => { if (fail) throw new Error('Unavailable'); return []; },
    });
    assert.equal(ui.nodes().find(node => node.type === 'NativeSelect').props.disabled, true);
    assert.deepEqual(ui.nodes().filter(node => node.type === 'option').map(node => node.props.value), ['']);
    assert.equal(ui.button('Bind to this connection').props.disabled, true);
    if (fail) assert.ok(ui.nodes().find(node => node.props.role === 'alert'));
    ui.close();
  }
});

test('live status shows only this source CDC and WAL, with explicitly cluster-wide Kafka lag', async () => {
  const saved = source('postgresql');
  const ui = await mount('SourceRuntimeStatus', saved, {
    fetchCdcStatus: async () => ({ checkedAt: '2026-11-26T09:00:00Z', outputMode: 'debezium',
      connect: { state: 'available', connectors: [
        { connectionId: saved.id, name: 'internal-current', state: 'RUNNING', taskStates: ['FAILED'] },
        { connectionId: 'other', name: 'private-other', state: 'RUNNING', taskStates: ['RUNNING'] },
      ] },
      postgres: { state: 'available', targets: [{ connectionId: saved.id, state: 'available' }], slots: [
        { connectionId: saved.id, name: 'current-slot', active: true },
        { connectionId: 'other', name: 'other-slot', active: true },
      ] }, kafka: { state: 'available', consumers: [{ group: 'cluster-worker', lag: 25, partitions: 3 }] },
    }),
  });
  for (const value of ['internal-current', 'FAILED', 'current-slot', 'Active reader', 'cluster-wide', '25 pending']) assert.ok(ui.text().includes(value), value);
  for (const value of ['private-other', 'other-slot']) assert.equal(ui.text().includes(value), false, value);
  ui.close();
});

test('failed live refresh clears previous running status instead of presenting stale success', async () => {
  let failed = false;
  const ui = await mount('SourceRuntimeStatus', source('postgresql'), {
    fetchCdcStatus: async () => {
      if (failed) throw new Error('Unavailable');
      return { checkedAt: '2026-11-26T09:00:00Z', connect: { state: 'available', connectors: [] }, postgres: { state: 'available', targets: [], slots: [] }, kafka: { state: 'available', consumers: [] } };
    },
  });
  assert.ok(ui.text().includes('No matching connector'));
  failed = true; ui.button('Refresh live status').props.onClick(); await ui.settle();
  assert.ok(ui.nodes().find(node => node.props.role === 'alert'));
  assert.equal(ui.text().includes('No matching connector'), false);
  ui.close();
});

test('capture operations report busy state to keep the enclosing dialog open', async () => {
  for (const [component, engine, fetchName, createName, status] of [
    ['SourceObjectEvents', 's3', 'fetchSourceObjectEvents', 'createSourceObjectEvents', { state: 'unconfigured', configurationHash: 'a'.repeat(64), notification: null }],
    ['SourceDamengCdc', 'dameng', 'fetchSourceDamengCdc', 'bindSourceDamengCdc', { state: 'unconfigured', schemaReady: true, binding: null }],
    ['SourceCdc', 'mongodb', 'fetchSourceCdc', 'createSourceCdc', { state: 'unconfigured', pluginAvailable: true, tasks: [], selection: null }],
  ]) {
    const changes = []; let resolve;
    const ui = await mount(component, source(engine), {
      [fetchName]: async () => status,
      [createName]: () => new Promise(done => { resolve = done; }),
    }, { onBusyChange: value => changes.push(value) });
    ui.submit(); await ui.settle(); assert.deepEqual(changes, [true]);
    resolve({}); await ui.settle(); assert.deepEqual(changes, [true, false]); ui.close();
  }
});

test('MinIO submits notification target and filters without row snapshot options', async () => {
  const ui = await mount('SourceObjectEvents', source('s3'), {
    fetchSourceObjectEvents: async () => ({ state: 'unconfigured', configurationHash: 'a'.repeat(64), notification: null }),
    createSourceObjectEvents: async () => ({}),
  });
  ui.fill('Existing MinIO Kafka target ARN', 'arn:minio:sqs::events:kafka');
  ui.fill('Object key prefix', 'incoming/'); ui.fill('Object key suffix', '.json');
  assert.equal(ui.nodes().some(node => node.type === 'NativeSelect'), false);
  ui.submit(); await ui.settle();
  const body = ui.calls.find(call => call.key === 'createSourceObjectEvents').args[1];
  assert.equal(body.targetArn, 'arn:minio:sqs::events:kafka');
  assert.equal(body.prefix, 'incoming/'); assert.equal(body.suffix, '.json');
  assert.equal(body.expectedConfigurationHash, 'a'.repeat(64));
  assert.equal(body.expectedUpdatedAt, source('s3').updatedAt);
  assert.equal('snapshot' in body, false); assert.equal('tables' in body, false);
  ui.close();
});

test('configured MinIO notification does not claim delivery or offer resumable pause', async () => {
  const ui = await mount('SourceObjectEvents', source('s3'), {
    fetchSourceObjectEvents: async () => ({ state: 'configured', configurationHash: 'a'.repeat(64), notification: { QueueArn: 'arn:minio:sqs::events:kafka', Events: ['s3:ObjectCreated:*'] } }),
    removeSourceObjectEvents: async () => ({}),
  });
  assert.ok(ui.text().includes('End-to-end delivery is not verified'));
  assert.equal(ui.button('Pause capture'), undefined);
  ui.button('Remove subscription').props.onClick(); await ui.settle();
  assert.equal(ui.calls.find(call => call.key === 'removeSourceObjectEvents').args[0], 'source-id');
  ui.close();
});

test('unprepared DMDRS environment disables binding and does not claim running capture', async () => {
  const ui = await mount('SourceDamengCdc', source('dameng'), {
    fetchSourceDamengCdc: async () => ({ state: 'unconfigured', schemaReady: false, binding: null }),
  });
  assert.ok(ui.button('Bind DMDRS output').props.disabled);
  assert.ok(ui.text().includes('Environment preparation required'));
  assert.ok(ui.text().includes('externally managed'));
  ui.close();
});

test('DMDRS binding retains explicit table selection and source revision', async () => {
  const ui = await mount('SourceDamengCdc', source('dameng'), {
    fetchSourceDamengCdc: async () => ({ state: 'unconfigured', schemaReady: true, binding: null }),
    bindSourceDamengCdc: async () => ({}),
  });
  ui.fill('Existing DMDRS Kafka output topic', 'dameng-capture');
  ui.fill('Expected tables', 'PUBLIC.ITEMS, PUBLIC.USERS'); ui.submit(); await ui.settle();
  assert.deepEqual(ui.calls.find(call => call.key === 'bindSourceDamengCdc').args,
    ['source-id', source('dameng').updatedAt, 'dameng-capture', ['PUBLIC.ITEMS', 'PUBLIC.USERS']]);
  assert.equal(ui.button('Pause capture'), undefined);
  ui.close();
});

test('Milvus form submits MQ, etcd and all selected physical channels', async () => {
  const ui = await mount('SourceCdc', source('milvus'), {
    fetchSourceCdc: async () => ({ state: 'unconfigured', pluginAvailable: true, tasks: [], selection: null }),
    createSourceCdc: async () => ({}),
  });
  ui.fill('Tables / collections', 'example.records'); ui.fill('Milvus MQ Kafka brokers', 'mq:9092');
  ui.fill('Milvus etcd endpoints', 'http://etcd:2379'); ui.fill('All physical channels', 'channel-0, channel-1');
  ui.submit(); await ui.settle();
  const body = ui.calls.find(call => call.key === 'createSourceCdc').args[1];
  assert.deepEqual(body.milvus, { kafkaBrokers: 'mq:9092', etcdEndpoints: ['http://etcd:2379'], etcdRoot: 'by-dev', pchannels: ['channel-0', 'channel-1'] });
  assert.deepEqual(body.tables, ['example.records']);
  ui.close();
});

test('missing Milvus plugin disables start despite valid saved database connection', async () => {
  const ui = await mount('SourceCdc', source('milvus'), {
    fetchSourceCdc: async () => ({ state: 'unconfigured', pluginAvailable: false, tasks: [], selection: null }),
  });
  assert.ok(ui.button('Start continuous capture').props.disabled);
  assert.ok(ui.text().includes('not installed on Kafka Connect'));
  ui.close();
});

test('CDC setup explains prerequisites, Kafka output and the selected historical-data mode', async () => {
  const ui = await mount('SourceCdc', source('postgresql'), {
    fetchSourceCdc: async () => ({ state: 'unconfigured', pluginAvailable: true, tasks: [], selection: null,
      prerequisite: 'Enable logical WAL and prepare a publication.' }),
    createSourceCdc: async () => ({}),
  });
  const steps = ui.nodes().find(node => node.type === 'ol' && node.props['aria-label'] === 'Continuous capture steps');
  assert.equal(nodes(steps).filter(node => node.type === 'li').length, 3);
  assert.match(ui.text(), /does not write tables into a destination database/);
  assert.match(ui.text(), /include rows already in the source/);
  ui.select('Initial data', 'no_data');
  assert.match(ui.text(), /Existing rows are skipped/);
  ui.fill('Tables / collections', 'public.records, public.users');
  ui.fill('Existing publication', 'prepared_publication'); ui.submit(); await ui.settle();
  assert.deepEqual(ui.calls.find(call => call.key === 'createSourceCdc').args, [
    'source-id', { tables: ['public.records', 'public.users'], snapshot: 'no_data', publication: 'prepared_publication' },
    source('postgresql').updatedAt,
  ]);
  ui.close();
});

test('configured CDC groups task state, Kafka output and controls and distinguishes pause from removal', async () => {
  const ui = await mount('SourceCdc', source('postgresql'), {
    fetchSourceCdc: async () => ({ state: 'RUNNING', pluginAvailable: true, sourceChanged: false,
      tasks: [{ id: 0, state: 'RUNNING' }], selection: { tables: ['public.records'], snapshot: 'initial' },
      topicPrefix: 'spider-cdc-source-generation', slot: 'replication_slot' }),
    controlSourceCdc: async () => ({}),
  });
  const output = ui.nodes().find(node => node.type === 'section' && node.props['aria-label'] === 'Capture status and output');
  assert.match(text(output), /Task 0RUNNING/);
  assert.match(text(output), /spider-cdc-source-generation/);
  assert.match(text(output), /Pause retains the source position/);
  assert.match(text(output), /existing Kafka messages and PostgreSQL replication slots remain/);
  assert.match(text(output), /does not verify downstream database writes/);
  ui.button('Pause capture').props.onClick(); await ui.settle();
  assert.deepEqual(ui.calls.find(call => call.key === 'controlSourceCdc').args,
    ['source-id', 'pause', source('postgresql').updatedAt]);
  ui.close();
});

test('DMDRS setup separates external preparation, output binding and independently verified health', async () => {
  const ui = await mount('SourceDamengCdc', source('dameng'), {
    fetchSourceDamengCdc: async () => ({ state: 'bound', schemaReady: true,
      binding: { topic: 'dameng-output', tables: ['PUBLIC.ITEMS'], sourceChanged: false } }),
  });
  const steps = ui.nodes().find(node => node.type === 'ol' && node.props['aria-label'] === 'DMDRS binding steps');
  assert.equal(nodes(steps).filter(node => node.type === 'li').length, 3);
  assert.match(ui.text(), /does not start DMDRS/);
  assert.match(ui.text(), /Choose the historical snapshot and log starting position in DMDRS/);
  assert.match(ui.text(), /upstream DMDRS task and its Kafka output continue running/);
  assert.equal(ui.button('Start continuous capture'), undefined);
  assert.equal(ui.button('Pause capture'), undefined);
  ui.close();
});

test('object events explain target preparation, key filters and delivery checks without promising a snapshot', async () => {
  const ui = await mount('SourceObjectEvents', source('s3'), {
    fetchSourceObjectEvents: async () => ({ state: 'unconfigured', configurationHash: 'a'.repeat(64), notification: null }),
  });
  const steps = ui.nodes().find(node => node.type === 'ol' && node.props['aria-label'] === 'Object notification steps');
  assert.equal(nodes(steps).filter(node => node.type === 'li').length, 3);
  assert.match(ui.text(), /Existing objects are not sent/);
  assert.match(ui.text(), /Leave both empty for all object keys/);
  assert.match(ui.text(), /Events during the disabled period cannot be replayed/);
  assert.match(ui.text(), /This page does not perform that test/);
  assert.equal(ui.nodes().some(node => node.type === 'NativeSelect'), false);
  ui.close();
});

test('capture forms report actual unsaved edits, clear after successful save and release on unmount', async () => {
  for (const [component, engine, fetchName, createName, status, field, value] of [
    ['SourceCdc', 'postgresql', 'fetchSourceCdc', 'createSourceCdc', { state: 'unconfigured', pluginAvailable: true, tasks: [], selection: null }, 'Tables / collections', 'public.records'],
    ['SourceDamengCdc', 'dameng', 'fetchSourceDamengCdc', 'bindSourceDamengCdc', { state: 'unconfigured', schemaReady: true, binding: null }, 'Existing DMDRS Kafka output topic', 'dameng-output'],
    ['SourceObjectEvents', 's3', 'fetchSourceObjectEvents', 'createSourceObjectEvents', { state: 'unconfigured', configurationHash: 'a'.repeat(64), notification: null }, 'Existing MinIO Kafka target ARN', 'arn:minio:sqs::events:kafka'],
  ]) {
    const changes = [];
    const ui = await mount(component, source(engine), { [fetchName]: async () => status, [createName]: async () => ({}) },
      { onDirtyChange: value => changes.push(value) });
    assert.equal(changes.at(-1), false, `${component} defaults must be clean`);
    ui.fill(field, value); assert.equal(changes.at(-1), true);
    ui.submit(); await ui.settle(); assert.equal(changes.at(-1), false, `${component} save must establish a new baseline`);
    ui.fill(field, value + '-changed'); assert.equal(changes.at(-1), true);
    ui.close(); assert.equal(changes.at(-1), false, `${component} unmount must release its dirty state`);
  }
});

test('default initial snapshot is clean and failed capture saves retain unsaved protection', async () => {
  const changes = [];
  const ui = await mount('SourceCdc', source('postgresql'), {
    fetchSourceCdc: async () => ({ state: 'unconfigured', pluginAvailable: true, tasks: [], selection: null }),
    createSourceCdc: async () => { throw new Error('Save rejected'); },
  }, { onDirtyChange: value => changes.push(value) });
  assert.equal(changes.at(-1), false);
  ui.select('Initial data', 'no_data'); assert.equal(changes.at(-1), true);
  ui.select('Initial data', 'initial'); assert.equal(changes.at(-1), false);
  ui.fill('Tables / collections', 'public.records'); ui.submit(); await ui.settle();
  assert.equal(changes.at(-1), true);
  ui.close();
});

test('restoring the same object event set clears unsaved state regardless of checkbox order', async () => {
  const changes = [];
  const ui = await mount('SourceObjectEvents', source('s3'), {
    fetchSourceObjectEvents: async () => ({ state: 'unconfigured', configurationHash: 'a'.repeat(64), notification: null }),
  }, { onDirtyChange: value => changes.push(value) });
  ui.check('Object created or overwritten', false); assert.equal(changes.at(-1), true);
  ui.check('Object created or overwritten', true); assert.equal(changes.at(-1), false);
  ui.close();
});
