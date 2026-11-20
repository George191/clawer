import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const compiled = ts.transpileModule(readFileSync(new URL('../src/components/ui/overlays.tsx', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.React, esModuleInterop: true },
}).outputText;

function load() {
  let now = 1800000000000, sequence = 0, tree;
  const timers = new Map();
  const setTimeout = (run, delay = 0) => {
    const id = ++sequence;
    timers.set(id, { run, at: now + delay });
    return id;
  };
  const clearTimeout = id => timers.delete(id);
  const react = {
    createElement: (type, props, ...children) => ({ type, props: { ...props, children } }),
    isValidElement: value => Boolean(value && typeof value === 'object' && 'type' in value && 'props' in value),
  };
  const context = {
    exports: {}, setTimeout, clearTimeout,
    Date: class extends Date { static now() { return now; } },
    window: { setTimeout, clearTimeout },
    document: { createElement: () => ({ dataset: {} }), body: { appendChild() {} } },
    require(name) {
      if (name === 'react') return { __esModule: true, default: react, ...react };
      if (name === 'react-dom/client') return { createRoot: () => ({ render: next => { tree = next; } }) };
      if (name === 'react-dom') return { createPortal: children => children };
      if (name === './icons') return Object.fromEntries(['AlertOutlined', 'CheckOutlined', 'CloseCircleOutlined', 'CloseOutlined', 'InfoCircleOutlined'].map(icon => [icon, icon]));
      if (name === './icons') return Object.fromEntries(['AlertOutlined', 'CheckOutlined', 'CloseCircleOutlined', 'CloseOutlined', 'InfoCircleOutlined'].map(icon => [icon, icon]));
      if (name.endsWith('.css')) return {};
      throw new Error(`Unexpected module ${name}`);
    },
  };
  vm.runInNewContext(compiled, context);
  function find(node, predicate, found = []) {
    if (Array.isArray(node)) node.forEach(child => find(child, predicate, found));
    else if (node && typeof node === 'object') {
      if (predicate(node)) found.push(node);
      find(node.props?.children, predicate, found);
    }
    return found;
  }
  const byClass = (className, node = tree) => find(node, item => item.props?.className?.split(' ').includes(className));
  return {
    ...context.exports,
    notices: () => byClass('ui-message-notice'),
    content: () => byClass('ui-message-notice-content')[0],
    close: () => byClass('ui-message-close')[0],
    advance(milliseconds) {
      const end = now + milliseconds;
      while (true) {
        const next = [...timers].filter(([, timer]) => timer.at <= end).sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0];
        if (!next) break;
        now = next[1].at;
        timers.delete(next[0]);
        next[1].run();
      }
      now = end;
    },
  };
}

test('replacing a key keeps one notice and completes both promises when the latest notice closes', async () => {
  const ui = load();
  let oldClosed = 0, newClosed = 0, oldFinished = 0, newFinished = 0;
  const first = ui.message.info({ key: 'job', content: 'Starting', duration: 1, onClose: () => oldClosed++ });
  first.then(() => oldFinished++);
  ui.advance(400);
  const latest = ui.message.success({ key: 'job', content: 'Completed', duration: 2, onClose: () => newClosed++ });
  latest.then(() => newFinished++);
  assert.equal(ui.notices().length, 1);
  assert.match(JSON.stringify(ui.content()), /Completed/);
  assert.doesNotMatch(JSON.stringify(ui.content()), /Starting/);
  ui.advance(600);
  assert.equal(ui.notices().length, 1, 'the replaced timer must not close the new notice');
  await Promise.resolve();
  assert.equal(oldFinished, 0);
  ui.advance(1399);
  assert.equal(ui.notices().length, 1);
  ui.advance(1);
  await Promise.all([first, latest]);
  assert.equal(ui.notices().length, 0);
  assert.deepEqual([oldClosed, newClosed, oldFinished, newFinished], [0, 1, 1, 1]);
});

test('the default close button resolves once without invoking the notice click action', async () => {
  const ui = load();
  let closed = 0, finished = 0, clicked = 0, stopped = 0;
  const result = ui.message.success({ content: 'Saved', duration: 2, onClose: () => closed++, onClick: () => clicked++ });
  result.then(() => finished++);
  const close = ui.close();
  assert.equal(close.props['aria-label'], 'Dismiss notification');
  close.props.onClick({ stopPropagation: () => stopped++ });
  close.props.onClick({ stopPropagation: () => stopped++ });
  result();
  ui.message.destroy();
  ui.advance(3000);
  await result;
  assert.equal(ui.notices().length, 0);
  assert.deepEqual([closed, finished, clicked, stopped], [1, 1, 0, 2]);
});

test('maxCount evicts the oldest notice and destroy completes each remaining notice once', async () => {
  const ui = load(), closed = [], finished = [];
  ui.message.config({ maxCount: 2 });
  const results = ['First', 'Second', 'Third'].map(content => {
    const result = ui.message.info({ content, duration: 0, onClose: () => closed.push(content) });
    result.then(() => finished.push(content));
    return result;
  });
  assert.equal(ui.notices().length, 2);
  assert.deepEqual(closed, ['First']);
  await Promise.resolve();
  assert.deepEqual(finished, ['First']);
  ui.message.destroy();
  ui.message.destroy();
  await Promise.all(results);
  assert.deepEqual(closed, ['First', 'Second', 'Third']);
  assert.deepEqual(finished, ['First', 'Second', 'Third']);
});

test('duration zero and the default loading duration persist until explicitly closed', async () => {
  const ui = load();
  const fixed = ui.message.info({ content: 'Persistent', duration: 0, closable: false });
  assert.equal(ui.close(), undefined);
  const loading = ui.message.loading('Loading');
  ui.advance(3600000);
  assert.equal(ui.notices().length, 2);
  fixed();
  assert.equal(ui.notices().length, 1);
  loading();
  await Promise.all([fixed, loading]);
  assert.equal(ui.notices().length, 0);
});

test('hover and focus pause independently and resume only the remaining lifetime', async () => {
  const ui = load();
  const result = ui.message.info({ content: 'Readable notification', duration: 2 });
  const content = ui.content();
  const inside = {}, outside = {};
  const currentTarget = { contains: target => target === inside };
  ui.advance(500);
  content.props.onMouseEnter();
  ui.advance(1000);
  content.props.onFocus();
  content.props.onMouseLeave();
  ui.advance(1000);
  assert.equal(ui.notices().length, 1, 'focus keeps the notice paused after pointer exit');
  content.props.onBlur({ currentTarget, relatedTarget: inside });
  ui.advance(500);
  assert.equal(ui.notices().length, 1, 'moving focus inside the notice must not resume its timer');
  content.props.onBlur({ currentTarget, relatedTarget: outside });
  ui.advance(1499);
  assert.equal(ui.notices().length, 1);
  ui.advance(1);
  await result;
  assert.equal(ui.notices().length, 0);
});

test('pointer hover keeps the notice paused after focus leaves', () => {
  const ui = load();
  ui.message.info({ content: 'Readable notification', duration: 1 });
  const content = ui.content();
  ui.advance(300);
  content.props.onFocus();
  content.props.onMouseEnter();
  content.props.onBlur({ currentTarget: { contains: () => false }, relatedTarget: null });
  ui.advance(2000);
  assert.equal(ui.notices().length, 1);
  content.props.onMouseLeave();
  ui.advance(699);
  assert.equal(ui.notices().length, 1);
  ui.advance(1);
  assert.equal(ui.notices().length, 0);
});

test('a throwing close callback cannot leave a removed notice on screen', async () => {
  const ui = load();
  const result = ui.message.error({ content: 'Failed', onClose: () => { throw new Error('Caller failed'); } });
  assert.throws(() => result(), /Caller failed/);
  assert.equal(ui.notices().length, 0);
  await result;
});
