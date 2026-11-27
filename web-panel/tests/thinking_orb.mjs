import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const jsx = (type, props) => ({ type, props });
const nodes = value => Array.isArray(value) ? value.flatMap(nodes) : value && typeof value === 'object'
  ? [value, ...nodes(value.props?.children)] : [];
function compile(source, context = {}) {
  const exports = {};
  const code = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText;
  vm.runInNewContext(code, { exports, require: name => name === 'react/jsx-runtime'
    ? { jsx, jsxs: jsx } : name === 'thinking-orbs' ? { ThinkingOrb: 'ThinkingOrb' } : {}, ...context });
  return exports;
}
function fragment(file, name) {
  const path = new URL('../src/pages/AICollect/' + file, import.meta.url);
  const source = readFileSync(path, 'utf8');
  const tree = ts.createSourceFile(path.pathname, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let result;
  function visit(node) {
    if (ts.isFunctionDeclaration(node) && node.name?.text === name) result = 'export ' + node.getText(tree);
    if (ts.isVariableDeclaration(node) && node.name.getText(tree) === name) result = 'export const ' + name + ' = ' + node.initializer.getText(tree);
    ts.forEachChild(node, visit);
  }
  visit(tree);
  assert.ok(result, name + ' was not found');
  return result;
}
const { LoadingOrb } = compile(readFileSync(new URL('../src/components/ui/loading.tsx', import.meta.url), 'utf8'));
const { StepProgress } = compile(fragment('ChatPreview.tsx', 'StepProgress'), {
  LoadingOrb, useState: value => [value, () => {}], useEffect() {},
  labels: { Thinking: 'Thinking', Working: 'Working', Paused: 'Paused' },
  steps: [{ title: 'Understanding the request', detail: 'Understand' }, { title: 'Searching', detail: 'Search' }],
});
const orbs = tree => nodes(tree).filter(node => node.type === LoadingOrb);

test('Thinking uses the reference composing effect without changing existing orb modes', () => {
  for (const [state, preset] of [['thinking', 'composing'], ['working', 'working'], ['solving', 'connecting']]) {
    const orb = LoadingOrb({ state, size: 20, paused: true });
    assert.equal(orb.props.state, preset);
    assert.equal(orb.props['data-loading-state'], state);
    assert.equal(orb.props.paused, true);
  }
});

test('chat steps select Thinking from the existing activity and remove it when stopped', () => {
  const props = { turn: { complete: false, step: 0, done: 0 }, current: true, running: true, expandSignal: 0 };
  assert.equal(orbs(StepProgress({ ...props, activity: 'Thinking' }))[0].props.state, 'thinking');
  assert.equal(orbs(StepProgress({ ...props, activity: 'Working' }))[0].props.state, 'solving');
  assert.equal(orbs(StepProgress({ ...props, activity: 'Paused', running: false })).length, 0);
  assert.equal(orbs(StepProgress({ ...props, activity: 'Ready', turn: { complete: true, step: 0, done: 1 }, running: false })).length, 0);
});

test('active AI analysis Thinking shows its orb, historical or non-thinking analysis does not', () => {
  function render(activity, active) {
    const { renderAnalysisFeed } = compile(fragment('index.tsx', 'renderAnalysisFeed'), {
      LoadingOrb, dotActivity: activity, analysisClock: 2000,
      analysisFeed: [{ id: 1, kind: 'thinking', step: 'fetch_page', content: 'Reasoning', createdAt: 1000, updatedAt: 1000 }],
      visibleTemplateStages: [], templateStageOrder: [], showAnalysisTiming: true,
      analyzeRequestRef: { current: active }, analyzeStepRef: { current: 'fetch_page' },
      expandedAnalysisSteps: new Set(), setExpandedAnalysisSteps() {},
    });
    return renderAnalysisFeed();
  }
  assert.equal(orbs(render('Thinking', true))[0].props.state, 'thinking');
  assert.equal(orbs(render('Working', true)).length, 0);
  assert.equal(orbs(render('Thinking', false)).length, 0);
});
