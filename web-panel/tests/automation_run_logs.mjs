import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
function load(path, dependencies = {}) {
  const exports = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText,
    { exports, require: name => dependencies[name] });
  return exports;
}
const notices = load('../src/pages/AICollect/workerNotices.ts');
const { mergeRunLogs, runLogPresentation } = load('../src/pages/AutomationCenter/runLogEvents.ts', { '@/pages/AICollect/workerNotices': notices });
const event = (id, message, run_id = 'current', created_at = '2026-11-25T15:00:00Z') => ({ id, message, run_id, created_at, level: 'info' });
const sync = event(2, '[2026-11-25 15:00:00,000: INFO/Syncer] RecordPublisher: sent → demo.topic record_id=record-1');
const download = event(3, '[2026-11-25 15:00:01,000: INFO/Downloader] DownloadWorker: record completed record_id=record-1 downloaded=2 reused=1 not_found=1 failed=0');
test('separate API notifications survive log pagination and remain outside console lines', () => {
  const initial = mergeRunLogs([event(1,'ordinary log')], [sync, download]);
  const result = runLogPresentation(mergeRunLogs(initial, [event(0,'older log')]), 'current');
  assert.equal(result.lines.length, 2);
  assert.equal(result.sync.recordId, 'record-1');
  assert.equal(result.sync.topic, 'demo.topic');
  assert.equal(result.download.itemsCompleted, 3);
  assert.equal(result.download.itemsMissing, 1);
  assert.equal(result.download.itemsTotal, 4);
});
test('historical batch selection cannot show current batch notifications', () => {
  const result = runLogPresentation([sync,download,event(4,'historical log','previous')], 'previous');
  assert.equal(result.sync,null); assert.equal(result.download,null); assert.equal(result.lines.length,1);
});
test('replayed older progress updates cannot replace a newer worker notice', () => {
  const newer = { ...sync, message: sync.message.replace('15:00:00','15:01:00') };
  const merged = mergeRunLogs([newer], [sync]);
  assert.equal(merged.length,1); assert.equal(merged[0].message,newer.message);
});
