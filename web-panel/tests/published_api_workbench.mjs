import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

function compile(file, require) {
  const source = readFileSync(new URL('../src/pages/Pipeline/' + file, import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const exports = {};
  vm.runInThisContext('(function(require,exports,window){' + code + '\n})')(require, exports, { setInterval: () => 1, clearInterval: () => {} });
  return exports;
}
const filters = compile('explorerFilters.ts', () => { throw new Error('Unexpected dependency'); });
const helpers = compile('publishedApiPresentation.ts', name => {
  if (name === './explorerFilters') return filters;
  throw new Error('Unexpected helper dependency ' + name);
});
const publication = (id, patch = {}) => ({ id, url: '/api/v1/etl/published/' + id + '/data', keyStatus: 'active', validFrom: '2020-01-01T00:00:00Z', validUntil: '2100-01-01T00:00:00Z', filterType: [], filterSource: [], ...patch });
const rule = (id, patch = {}) => ({ id, field: '', operator: '=', value: '', active: true, join: 'and', ...patch });
const children = tree => [tree?.props?.children].flat(Infinity).filter(value => value !== null && value !== undefined && value !== false);
const nodes = tree => tree && typeof tree === 'object' ? [tree, ...children(tree).flatMap(nodes)] : [];
const copy = tree => typeof tree === 'string' || typeof tree === 'number' ? String(tree) : children(tree).map(copy).join('');

function mount(props) {
  const state = [], effects = [], refs = [];
  let tree, cursor, dirty;
  const jsx = (type, props) => ({ type, props });
  const module = compile('PublishedApiWorkbench.tsx', name => {
    if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx };
    if (name === 'react') return {
      useState(initial) { const index = cursor++; if (!(index in state)) state[index] = initial; return [state[index], update => { const next = typeof update === 'function' ? update(state[index]) : update; if (!Object.is(next, state[index])) { state[index] = next; dirty = true; } }]; },
      useRef(initial) { const index = cursor++; return refs[index] ||= { current: initial }; },
      useEffect(effect, deps) { const index = cursor++; if (!effects[index] || deps.some((value, i) => !Object.is(value, effects[index][i]))) { effects[index] = deps; pending.push(effect); } },
    };
    if (name === '@/components/ui') return { Button: 'Button', Input: 'Input', Select: 'Select', message: {} };
    if (name === '@/components/ui/icons') return new Proxy({}, { get: (_, key) => String(key) });
    if (name === '../../services/endpoints') return { apiUrl: path => 'https://api.example.test' + path };
    if (name === './publishedApiPresentation') return helpers;
    if (name.endsWith('.css')) return {};
    throw new Error('Unexpected component dependency ' + name);
  });
  const pending = [];
  const render = () => { do { dirty = false; cursor = 0; pending.length = 0; tree = module.default(props); pending.splice(0).forEach(effect => effect()); } while (dirty); return tree; };
  render();
  return { view: () => tree, click(label) { const button = nodes(tree).find(node => node.type === 'button' && (node.props['aria-label'] === label || copy(node).trim() === label)); assert.ok(button, 'Missing button: ' + label); button.props.onClick(); return render(); } };
}

test('publication status distinguishes key state and the validity window', () => {
  const api = publication('a'), now = Date.parse('2026-11-21T10:00:00Z');
  assert.equal(helpers.publicationState(api, now).label, 'Active');
  assert.equal(helpers.publicationState({ ...api, keyStatus: 'unissued' }, now).label, 'Key not issued');
  assert.equal(helpers.publicationState({ ...api, keyStatus: 'revoked' }, now).label, 'Key revoked');
  assert.equal(helpers.publicationState({ ...api, validFrom: '2030-01-01' }, now).label, 'Scheduled');
  assert.equal(helpers.publicationState({ ...api, validUntil: '2025-01-01' }, now).label, 'Expired');
  assert.equal(helpers.publicationState({ ...api, validUntil: '' }, now).label, 'Invalid validity');
});

test('readable range preserves parentheses, OR and disabled-group semantics', () => {
  const api = publication('range', { filters: [rule('group-open-a'), rule('city', { field: 'city', value: 'Shanghai', join: 'or' }),
    rule('staff', { field: 'employees', operator: '>=', value: '50' }), rule('group-close-a'),
    rule('group-open-hidden', { active: false }), rule('secret', { field: 'hidden', value: 'never' }), rule('group-close-hidden'),
    rule('active', { field: 'active', value: 'true' })] });
  assert.equal(helpers.publicationPredicate(api), '(city = "Shanghai" OR employees >= "50") AND active = "true"');
});

test('request examples use the existing endpoint, X-API-Key and supported pagination parameters', () => {
  const endpoint = 'https://api.example.test/v1/etl/published/test/data';
  const curl = helpers.publicationRequest(endpoint, 'curl');
  assert.ok(curl.includes(endpoint)); assert.ok(curl.includes('X-API-Key: $API_KEY'));
  assert.ok(curl.includes("'limit=50'")); assert.ok(curl.includes("'offset=0'"));
  assert.ok(!curl.includes('Bearer'));
  assert.ok(helpers.publicationRequest(endpoint, 'http').startsWith('GET /v1/etl/published/test/data?limit=50&offset=0 HTTP/1.1'));
});

test('validity input retains the existing local datetime conversion', () => {
  const local = '2026-11-21T18:30', utc = new Date(local).toISOString();
  assert.equal(helpers.publicationDateInput(utc), local);
  assert.equal(helpers.publicationDateValue(local), utc);
  assert.equal(helpers.publicationDateValue(''), '');
  assert.equal(helpers.publicationDateValue('invalid'), '');
});

test('nonempty API catalog selects each publication and exposes its own range and request', () => {
  const a = publication('first'), b = publication('second', { search: 'Needle', filters: [rule('city', { field: 'city', value: 'Tokyo' })] });
  const ui = mount({ items: [a, b] });
  assert.ok(copy(ui.view()).includes('https://api.example.test' + a.url));
  ui.click('Select API second');
  assert.ok(copy(ui.view()).includes('https://api.example.test' + b.url));
  ui.click('Data range'); assert.ok(copy(ui.view()).includes('city = "Tokyo"')); assert.ok(copy(ui.view()).includes('Needle'));
  ui.click('Request'); assert.ok(copy(ui.view()).includes('X-API-Key: $API_KEY'));
  assert.equal(nodes(ui.view()).filter(node => node.type === 'Input' || node.type === 'Select').length, 0, 'readonly source view has no mutation fields');
});

test('save, discard and delete remain controlled by the original management callbacks and dirty state', () => {
  let saves = 0, discards = 0, deleted;
  const api = publication('managed');
  const ui = mount({ items: [api], dirty: true, onSave: () => saves++, onDiscard: () => discards++, onDelete: value => { deleted = value; }, onUpdate() {} });
  const save = nodes(ui.view()).find(node => node.type === 'Button' && copy(node) === 'Save changes');
  assert.equal(save.props.disabled, false); save.props.onClick(); assert.equal(saves, 1);
  const discard = nodes(ui.view()).find(node => node.type === 'Button' && copy(node) === 'Discard changes');
  assert.equal(discard.props.disabled, false); discard.props.onClick(); assert.equal(discards, 1);
  const remove = nodes(ui.view()).find(node => node.type === 'button' && copy(node).trim() === 'Delete publication');
  assert.equal(remove.props.disabled, true); assert.equal(deleted, undefined);
  const clean = mount({ items: [api], onDiscard: () => discards++, onDelete: value => { deleted = value; } }); clean.click('Delete publication'); assert.equal(deleted, api);
  assert.equal(nodes(clean.view()).some(node => node.type === 'Button' && copy(node) === 'Discard changes'), false);
});

test('a failed API load is not presented as an empty successful catalog', () => {
  const ui = mount({ items: [], error: 'Read failed', onReload() {} });
  assert.ok(copy(ui.view()).includes('Unable to load published APIs'));
  assert.ok(!copy(ui.view()).includes('No published APIs'));
});
