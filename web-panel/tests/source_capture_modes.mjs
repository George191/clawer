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
    submit() { nodes(tree).find(node => node.type === 'form').props.onSubmit({ preventDefault() {} }); render(); },
    async settle() { for (let i = 0; i < 4; i++) { await new Promise(resolve => setImmediate(resolve)); render(); } },
    close() { for (const effect of effects) effect?.cleanup?.(); },
  };
  render(); await ui.settle(); return ui;
}

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
