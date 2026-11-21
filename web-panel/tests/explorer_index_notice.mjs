import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/pages/Pipeline/ExplorerFilterIndexNotice.tsx', import.meta.url), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: {
  module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
} }).outputText;
const scope = { schema: 'ts_ods', table: 'companies' };
const rule = { id: 'employees-filter', field: 'employees', operator: '>=', value: '50', active: true, join: 'and' };
const filterProps = { ...scope, rule, columnTypes: { employees: 'integer' } };
const sortProps = { ...scope, sorts: [{ field: 'employees', direction: 'asc' }, { field: 'city', direction: 'desc' }] };
const children = node => [node?.props?.children].flat(Infinity).filter(item => item !== undefined && item !== null);
const nodes = tree => tree && typeof tree === 'object' ? [tree, ...children(tree).flatMap(nodes)] : [];
const copy = tree => typeof tree === 'string' ? tree : children(tree).map(copy).join('');
const warning = tree => nodes(tree).some(node => node.props?.className?.split(' ').includes('is-warning'));
const warningIcon = tree => nodes(tree).some(node => node.type === 'WarningOutlined');

// Run the real component with deterministic hook lifecycles, timers and deferred catalog responses.
function mount(component, initialProps) {
  let props = initialProps, tree, cursor, dirty, identity = 'reader-a', session = { authenticated: true, locked: false, identity };
  const state = [], effects = [], pendingEffects = [], timers = new Map(), calls = [];
  let nextTimer = 0, writes = 0;
  const render = nextProps => {
    props = nextProps ?? props;
    do {
      dirty = false; cursor = 0; pendingEffects.length = 0;
      const root = exports[component](props);
      tree = root && typeof root.type === 'function' ? root.type(root.props) : root;
      for (const [index, effect, deps] of pendingEffects) {
        effects[index]?.cleanup?.();
        effects[index] = { deps, cleanup: effect() };
      }
    } while (dirty);
    return tree;
  };
  const jsx = (type, props) => ({ type, props });
  const require = name => {
    if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx };
    if (name === 'react') return {
      useState(initial) {
        const index = cursor++;
        if (!(index in state)) state[index] = initial;
        return [state[index], update => {
          const value = typeof update === 'function' ? update(state[index]) : update;
          if (!Object.is(value, state[index])) { state[index] = value; writes++; dirty = true; }
        }];
      },
      useEffect(effect, deps) {
        const index = cursor++;
        if (!effects[index] || deps.some((dep, i) => !Object.is(dep, effects[index].deps[i]))) pendingEffects.push([index, effect, deps]);
      },
    };
    if (name === '@/components/ui') return { Tooltip: 'Tooltip' };
    if (name === '@/components/ui/icons') return { WarningOutlined: 'WarningOutlined', ReloadOutlined: 'ReloadOutlined' };
    if (name === './SandboxMarkings') return { useSandboxIdentity: () => identity };
    if (name === '../../services/session') return { getSessionState: () => session };
    if (name === '../../services/api') return {
      checkPreviewIndexCoverage(schema, table, input, signal) {
        return new Promise((resolve, reject) => calls.push({ schema, table, input, signal, resolve, reject }));
      },
    };
    if (name.endsWith('.css')) return {};
    throw new Error('Unexpected component dependency: ' + name);
  };
  const exports = {};
  const window = { setTimeout: callback => { timers.set(++nextTimer, callback); return nextTimer; }, clearTimeout: id => timers.delete(id) };
  vm.runInThisContext('(function(require,exports,window){' + code + '\n})')(require, exports, window);
  render();
  return {
    calls, render, view: () => tree, writes: () => writes,
    runTimers() { const callbacks = [...timers.values()]; timers.clear(); callbacks.forEach(callback => callback()); },
    async settle(index, state) { calls[index].resolve({ state }); await Promise.resolve(); return render(); },
    async fail(index) { calls[index].reject(new Error('catalog unavailable')); await Promise.resolve(); return render(); },
    changeIdentity(value) { identity = value; session = { ...session, identity }; return render(); },
    lockSession() { session = { ...session, locked: true }; },
    unmount() { effects.forEach(effect => effect?.cleanup?.()); tree = null; },
  };
}

test('sort advisories send the complete ordered shape and recheck changed directions', async () => {
  const ui = mount('ExplorerSortIndexNotice', sortProps);
  ui.runTimers();
  assert.deepEqual(ui.calls[0].input, { sorts: sortProps.sorts });
  assert.equal(ui.calls[0].schema, scope.schema);
  assert.equal(ui.calls[0].table, scope.table);
  const changed = { ...sortProps, sorts: [{ field: 'employees', direction: 'desc' }, sortProps.sorts[1]] };
  ui.render(changed); ui.runTimers();
  assert.equal(ui.calls[0].signal.aborted, true);
  assert.deepEqual(ui.calls[1].input, { sorts: changed.sorts });
  assert.equal(await ui.settle(1, 'candidate'), null);
});

test('filter checks send only field and operator, retaining the metadata result across literal edits', async () => {
  const ui = mount('default', filterProps);
  ui.runTimers();
  assert.deepEqual(ui.calls[0].input, { field: 'employees', operator: 'gte' });
  assert.equal(await ui.settle(0, 'candidate'), null);
  assert.equal(ui.render({ ...filterProps, rule: { ...rule, value: '75' } }), null);
  ui.runTimers();
  assert.equal(ui.calls.length, 1);
});

test('grouped and OR filter rows are checked independently using only their field and operator', async () => {
  for (const [id, join] of [['plain-or', 'or'], ['group-filter-a', 'and'], ['group-filter-nested-b', 'or']]) {
    const ui = mount('default', { ...filterProps, rule: { ...rule, id, join } });
    ui.runTimers();
    assert.equal(ui.calls.length, 1);
    assert.deepEqual(ui.calls[0].input, { field: 'employees', operator: 'gte' });
    const tree = await ui.settle(0, 'no_candidate');
    assert.equal(warning(tree), true);
    assert.ok(copy(tree.props.title).includes('independently'));
  }
});

test('changing a row connector does not discard its result or repeat the same catalog check', async () => {
  const ui = mount('default', filterProps);
  ui.runTimers();
  assert.equal(warning(await ui.settle(0, 'no_candidate')), true);
  for (const join of ['or', 'and']) {
    assert.equal(warning(ui.render({ ...filterProps, rule: { ...rule, join } })), true);
    ui.runTimers();
    assert.equal(ui.calls.length, 1);
    assert.equal(ui.calls[0].signal.aborted, false);
  }
});

test('disabled rows, group boundaries and incomplete filters do not request an index check', () => {
  for (const patch of [{ active: false }, { id: 'group-open-a' }, { id: 'group-close-a' },
    { field: '' }, { value: '' }, { operator: '介于', value: '10,' }]) {
    const ui = mount('default', { ...filterProps, rule: { ...rule, ...patch } });
    ui.runTimers();
    assert.equal(ui.calls.length, 0);
    assert.equal(ui.view(), null);
  }
});

test('confirmed missing filter and sort indexes display warning icons and slow-query advisories', async () => {
  for (const [component, props, expected] of [
    ['default', filterProps, 'No leading index'], ['ExplorerSortIndexNotice', sortProps, 'No matching sort index'],
  ]) {
    const ui = mount(component, props); ui.runTimers();
    const tree = await ui.settle(0, 'no_candidate');
    assert.equal(warning(tree), true);
    assert.equal(warningIcon(tree), true);
    assert.ok(copy(tree).includes(expected));
    assert.ok(copy(tree).includes('may be slow'));
  }
});

test('unavailable checks have a warning icon and retry without claiming a missing index', async () => {
  const ui = mount('ExplorerSortIndexNotice', sortProps); ui.runTimers();
  const tree = await ui.fail(0);
  assert.equal(warning(tree), true);
  assert.equal(warningIcon(tree), true);
  assert.equal(copy(tree), 'Index check unavailable');
  const retry = nodes(tree).find(node => node.type === 'button');
  assert.equal(retry.props['aria-label'], 'Retry sort index check');
  retry.props.onClick(); ui.render(); ui.runTimers();
  assert.equal(ui.calls.length, 2);
  assert.deepEqual(ui.calls[1].input, { sorts: sortProps.sorts });
  assert.equal(await ui.settle(1, 'candidate'), null);
});

test('unsupported conditions remain neutral and never claim an index is missing', async () => {
  const unsupported = mount('default', { ...filterProps, columnTypes: { employees: 'jsonb' } });
  unsupported.runTimers();
  assert.equal(unsupported.calls.length, 0);
  assert.equal(copy(unsupported.view()), 'Index not assessed');
  assert.equal(warning(unsupported.view()), false);
  assert.match(unsupported.view().props.title, /field type.*jsonb/i);
  const checked = mount('ExplorerSortIndexNotice', sortProps); checked.runTimers();
  const tree = await checked.settle(0, 'unsupported');
  assert.equal(copy(tree), 'Index not assessed');
  assert.equal(warning(tree), false);
  assert.equal(warningIcon(tree), false);
});

test('unsupported filter operators explain their limitation without claiming a missing index', () => {
  for (const [operator, value] of [['包含', 'Acme'], ['是  null', '']]) {
    const ui = mount('default', { ...filterProps, rule: { ...rule, operator, value } });
    ui.runTimers();
    assert.equal(ui.calls.length, 0);
    assert.equal(copy(ui.view()), 'Index not assessed');
    assert.equal(warning(ui.view()), false);
    assert.match(ui.view().props.title, /operator/i);
    assert.match(ui.view().props.title, /not supported/i);
  }
});

test('common scalar filter types receive the same warning and full immediate tooltip as sorts', async () => {
  for (const type of ['text', 'character varying(160)', 'numeric(12,2)', 'uuid', 'boolean', 'timestamp without time zone']) {
    const ui = mount('default', { ...filterProps, columnTypes: { employees: type } });
    ui.runTimers();
    assert.deepEqual(ui.calls[0].input, { field: 'employees', operator: 'gte' });
    const tree = await ui.settle(0, 'no_candidate');
    assert.equal(warning(tree), true);
    assert.equal(warningIcon(tree), true);
    assert.equal(tree.props.mouseEnterDelay, 0);
    assert.equal(tree.props.overlayClassName, 'explorer-index-tooltip');
    assert.ok(copy(tree.props.title).includes('No leading index · may be slow'));
    assert.ok(copy(tree.props.title).includes('not a query plan or a timeout prediction'));
  }
});

test('cancelled and locked-session responses cannot publish stale results', async () => {
  const cancelled = mount('ExplorerSortIndexNotice', sortProps); cancelled.runTimers(); cancelled.unmount();
  const priorWrites = cancelled.writes();
  assert.equal(cancelled.calls[0].signal.aborted, true);
  cancelled.calls[0].resolve({ state: 'no_candidate' }); await Promise.resolve();
  assert.equal(cancelled.writes(), priorWrites);
  assert.equal(cancelled.view(), null);
  const locked = mount('ExplorerSortIndexNotice', sortProps); locked.runTimers(); locked.lockSession();
  const tree = await locked.settle(0, 'no_candidate');
  assert.equal(warning(tree), false);
});

test('identity or shape changes cannot reuse stale warnings while replacement checks are pending', async () => {
  const ui = mount('ExplorerSortIndexNotice', sortProps); ui.runTimers();
  assert.equal(warning(await ui.settle(0, 'no_candidate')), true);
  ui.render({ ...sortProps, sorts: [{ field: 'city', direction: 'asc' }] }); ui.runTimers();
  assert.equal(warning(ui.view()), false);
  ui.render(sortProps); ui.runTimers();
  assert.equal(warning(ui.view()), false);
  ui.changeIdentity('reader-b'); ui.runTimers();
  assert.equal(ui.calls.at(-2).signal.aborted, true);
  assert.equal(warning(await ui.settle(2, 'no_candidate')), false);
  assert.equal(await ui.settle(3, 'candidate'), null);
});
