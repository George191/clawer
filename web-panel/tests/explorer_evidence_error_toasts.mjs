import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const jsx = (type, props) => ({ type, props });
const descendants = tree => tree && typeof tree === 'object' ? [tree, ...[tree.props?.children].flat(Infinity).flatMap(descendants)] : [];
const text = tree => typeof tree === 'string' || typeof tree === 'number' ? String(tree)
  : tree && typeof tree === 'object' ? [tree.props?.children].flat(Infinity).map(text).join(' ') : '';
const rejected = () => Promise.reject(new Error('Unavailable'));

function view(file, api, props = {}) {
  let cursor = 0, tree, notices;
  const states = [], refs = [], effects = [], scheduled = [];
  const session = { identity: 'qa', authenticated: true, locked: false };
  const exports = {};
  const require = name => {
    if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx };
    if (name === 'react') return {
      useId: () => 'qa-heading',
      useState(initial) { const index = cursor++; if (!(index in states)) states[index] = typeof initial === 'function' ? initial() : initial;
        return [states[index], value => { states[index] = typeof value === 'function' ? value(states[index]) : value; }]; },
      useRef(initial) { const index = cursor++; return refs[index] ??= { current: initial }; },
      useEffect(effect, deps) { const index = cursor++; if (!effects[index] || deps.some((dep, i) => !Object.is(dep, effects[index].deps[i]))) scheduled.push([index, effect, deps]); },
    };
    if (name === './useExplorerErrorToast') return { default: (error, title, retry) => { if (error) notices.push({ error, title, retry }); } };
    if (name === '../../services/api') return api;
    if (name === '../../services/session') return { getSessionState: () => session };
    if (name === './SandboxMarkings') return { useSandboxIdentity: () => session.identity, sandboxScopeDenied: () => false };
    if (name === '@/components/ui') return { Button: 'Button', Input: 'Input', Modal: 'Modal' };
    if (name === 'react-router-dom') return { Link: 'Link', useSearchParams: () => [new URLSearchParams(), () => {}] };
    if (name === './transferReceiptFocus') return { exactTransferReceiptFocus: () => null, canonicalTransferReceipt: value => Boolean(value) };
    if (name === './ExplorerAssetQualityChecks') return { default: 'QualityChecks', readQualityDraft: () => ({}), qualityBounds: () => ({ valid: true }) };
    if (name === './TableExportEvidence') return { default: 'ExportEvidence', TABLE_EXPORT_SOURCE: { schema: 'ts_table_export_sandbox', table: 'records' } };
    return { default: name };
  };
  const code = ts.transpileModule(readFileSync(new URL('../src/pages/Pipeline/' + file + '.tsx', import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  vm.runInThisContext('(function(require,exports){' + code + '\n})')(require, exports);
  const render = () => {
    cursor = 0; scheduled.length = 0; notices = [];
    tree = exports.default({ schema: 'ts_transfer_sandbox', table: 'records', version: 0, canManage: true, onLatestCurrentReceipt() {}, ...props });
    if (typeof tree?.type === 'function') tree = tree.type(tree.props);
    for (const [index, effect, deps] of scheduled) { effects[index]?.cleanup?.(); effects[index] = { deps, cleanup: effect() }; }
    return tree;
  };
  const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); return render(); };
  render();
  return { render, flush, tree: () => tree, notices: () => notices,
    button: label => descendants(tree).find(node => node.type === 'Button' && node.props.children === label),
    unmount: () => effects.forEach(effect => effect?.cleanup?.()) };
}

test('table evidence failures stay blocked and toast retry only repeats the read', async () => {
  let reads = 0;
  const ui = view('ExplorerTableEvidence', { fetchExplorerTableEvidence: async () => { reads++; return rejected(); } });
  await ui.flush();
  assert.equal(ui.notices()[0].title, 'Evidence unavailable');
  assert.equal(descendants(ui.tree()).filter(node => node.props?.role === 'alert').length, 0);
  assert.doesNotMatch(text(ui.tree()), /Loading table evidence|No verified run history/);
  ui.notices()[0].retry(); ui.render(); await ui.flush();
  assert.equal(reads, 2);
});

test('export failures do not render successful empty-state claims', async () => {
  const ui = view('TableExportEvidence', { fetchTableExportLineage: rejected }, { mode: 'explorer' });
  await ui.flush();
  assert.equal(ui.notices()[0].title, 'Export evidence unavailable');
  assert.doesNotMatch(text(ui.tree()), /No saved export review|No receiver-confirmed receipts|Loading export lineage/);
});

test('canceled evidence reads do not publish a failure', async () => {
  let reject, signal;
  const ui = view('ExplorerTableEvidence', { fetchExplorerTableEvidence: (_schema, _table, request) => {
    signal = request; return new Promise((_resolve, fail) => { reject = fail; });
  } });
  ui.unmount(); reject(new Error('Canceled')); await ui.flush();
  assert.equal(signal.aborted, true);
  assert.equal(ui.notices().length, 0);
});

test('CDC API failures are distinct from a successful unavailable observation', async () => {
  const unavailable = view('ExplorerCdcCapture', { fetchTableCapture: async () => ({ state: 'unavailable', reason: 'Capture is not configured.' }) });
  await unavailable.flush();
  assert.equal(unavailable.notices().length, 0);
  assert.match(text(unavailable.tree()), /Capture is not configured/);
  const failed = view('ExplorerCdcCapture', { fetchTableCapture: rejected });
  await failed.flush();
  assert.equal(failed.notices()[0].title, 'Change capture unavailable');
  assert.doesNotMatch(text(failed.tree()), /Inspecting retained changes|Capture is not configured/);
});

for (const [file, read, title] of [
  ['ExplorerFieldFreshness', 'fetchFieldFreshness', 'Field freshness unavailable'],
  ['ExplorerMinimumRowHealth', 'fetchMinimumRowHealth', 'Minimum row check unavailable'],
  ['ExplorerDailyJobQuality', 'fetchDailyJobQuality', 'Scheduled quality unavailable'],
  ['ExplorerAssetStatistics', 'fetchCollectedAssetStatistics', 'Statistics unavailable'],
]) test(file + ' reports request failures even when its card is hidden', async () => {
  const ui = view(file, { [read]: rejected }); await ui.flush();
  assert.equal(ui.notices()[0].title, title);
  assert.equal(typeof ui.notices()[0].retry, 'function');
  assert.equal(ui.tree(), null);
});

test('timeline paging failures preserve prior entries and retain local filter validation', async () => {
  let reads = 0;
  const ui = view('ExplorerTransferTimeline', { fetchTransferTimeline: async () => {
    if (reads++) return rejected();
    return { entries: [{ kind: 'attempt', id: 'one' }], nextCursor: 'older', latestCurrentReceipt: null };
  } }, { onLatestCurrentReceipt: () => {} });
  await ui.flush(); await ui.button('Load older entries').props.onClick(); await ui.flush();
  assert.equal(ui.notices()[0].title, 'Timeline page unavailable');
  assert.equal(descendants(ui.tree()).filter(node => node.props?.['aria-label'] === 'Transfer timeline entries').length, 1);
  const from = descendants(ui.tree()).find(node => node.props?.['aria-label'] === 'From (UTC)');
  from.props.onChange({ target: { value: '2026-11-20T00:00' } }); ui.render();
  descendants(ui.tree()).find(node => node.type === 'form').props.onSubmit({ preventDefault() {} }); ui.render();
  assert.match(text(descendants(ui.tree()).find(node => node.props?.role === 'alert')), /Choose both UTC times/);
  assert.equal(reads, 2);
});

test('a field policy write failure uses a toast without replaying the write', async () => {
  let writes = 0;
  const ui = view('ExplorerFieldFreshness', {
    fetchFieldFreshness: async () => ({ enabled: true, state: 'superseded', maxLagSeconds: 60, policyGeneration: 1, generation: 1 }),
    reviewFieldFreshness: async () => { writes++; return rejected(); },
  });
  await ui.flush(); ui.button('Review field freshness check').props.onClick(); ui.render();
  await descendants(ui.tree()).find(node => node.type === 'Modal').props.onOk(); await ui.flush();
  assert.equal(writes, 1);
  const notification = ui.notices().find(notice => notice.title === 'Field freshness update failed');
  assert.ok(notification);
  assert.equal(notification.retry, undefined);
  assert.equal(descendants(ui.tree()).filter(node => node.props?.role === 'alert').length, 0);
});

test('freshness read retries do not replay a policy operation', async () => {
  let reads = 0;
  const ui = view('ExplorerAssetFreshness', {
    fetchAssetFreshness: async () => { reads++; return rejected(); },
    fetchAssetFreshnessCandidates: async () => [],
  });
  await ui.flush();
  assert.equal(ui.notices()[0].title, 'Freshness check unavailable');
  assert.doesNotMatch(text(ui.tree()), /Loading freshness check|No eligible saved transfer/);
  ui.notices()[0].retry(); ui.render(); await ui.flush();
  assert.equal(reads, 2);
});

test('weekly evidence keeps unsuccessful verification separate from request failure', async () => {
  const props = { identity: 'qa', generation: 1, validUntil: '2099-01-01T00:00:00Z', outcome: 'healthy',
    observation: { slot: '2026-11-22T00:00:00Z', finishedAt: '2026-11-22T00:01:00Z', rowsAffected: 2, receiptId: 'receipt' } };
  const mismatch = view('ExplorerWeeklyRunEvidence', { fetchWeeklyJobEvidence: async () => ({ kind: 'receipt', slot: 'other' }) }, props);
  await mismatch.button('Verify run evidence').props.onClick(); await mismatch.flush();
  assert.equal(mismatch.notices().length, 0);
  assert.match(text(mismatch.tree()), /Verified run evidence is unavailable/);
  const failed = view('ExplorerWeeklyRunEvidence', { fetchWeeklyJobEvidence: rejected }, props);
  await failed.button('Verify run evidence').props.onClick(); await failed.flush();
  assert.equal(failed.notices()[0].title, 'Run evidence unavailable');
  assert.doesNotMatch(text(failed.tree()), /Verified run evidence is unavailable/);
});
