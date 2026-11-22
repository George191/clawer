import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const jsx = (type, props) => ({ type, props });
const nodes = tree => Array.isArray(tree) ? tree.flatMap(nodes) : tree && typeof tree === 'object'
  ? [tree, ...nodes(tree.props?.children)] : [];
function compile(file, require) {
  const source = readFileSync(new URL('../src/pages/Pipeline/' + file, import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const exports = {};
  vm.runInThisContext('(function(require,exports){' + code + '\n})')(require, exports);
  return exports;
}

function workspace() {
  let current;
  const instance = () => ({ cells: [], effects: [], scheduled: [], cursor: 0 });
  const hooks = {
    createContext: initial => ({ current: initial, Provider: 'OverlayProvider' }),
    useContext: context => context.current,
    useState(initial) {
      const i = current.cursor++, owner = current;
      if (!owner.cells[i]) {
        const cell = { value: typeof initial === 'function' ? initial() : initial };
        cell.set = value => { cell.value = typeof value === 'function' ? value(cell.value) : value; };
        owner.cells[i] = cell;
      }
      const cell = owner.cells[i]; return [cell.value, cell.set];
    },
    useRef(initial) { return current.cells[current.cursor++] ||= { current: initial }; },
    useId() { current.cursor++; return 'overlay-test-selection'; },
    useCallback(callback, deps) {
      const i = current.cursor++, previous = current.cells[i];
      if (!previous || deps.some((value, index) => !Object.is(value, previous.deps[index]))) current.cells[i] = { callback, deps };
      return current.cells[i].callback;
    },
    useEffect(effect, deps) {
      const i = current.cursor++, previous = current.effects[i];
      if (!previous || !deps || deps.some((value, index) => !Object.is(value, previous.deps?.[index]))) current.scheduled.push([i, effect, deps]);
    },
  };
  const render = (owner, fn, props) => {
    current = owner; owner.cursor = 0; owner.scheduled = [];
    const tree = fn(props);
    for (const [i, effect, deps] of owner.scheduled) { owner.effects[i]?.cleanup?.(); owner.effects[i] = { deps, cleanup: effect() }; }
    current = undefined;
    return tree;
  };
  const unmount = owner => owner.effects.forEach(effect => effect?.cleanup?.());
  const context = compile('ExplorerSelectionOverlayContext.ts', () => hooks);
  const ui = { Button: 'Button', NativeSelect: 'NativeSelect', Table: 'Table', Checkbox: 'Checkbox', Dropdown: 'Dropdown', Input: 'Input', Select: 'Select', message: { warning() {}, success() {}, error() {} } };
  const base = name => name === 'react' ? hooks : name === 'react/jsx-runtime' ? { jsx, jsxs: jsx, Fragment: 'Fragment' }
    : name === '@/components/ui' ? ui : name === '@/components/ui/icons' ? new Proxy({}, { get: (_, key) => String(key) })
      : name === './ExplorerSelectionOverlayContext' ? context : undefined;
  const overview = { ExplorerObjectOverview: 'Overview', ExplorerTypeIcon: 'TypeIcon', ExplorerFieldValue: 'FieldValue',
    explorerRowTitle: record => record.name, explorerFields: () => [], propertyValue: (row, field) => row.record[field] };
  const selection = compile('ExplorerSelection.tsx', name => base(name) ?? (name === './ExplorerObjectOverview' ? overview
    : name === './ExplorerValue' ? { ExplorerValueProvider: 'ValueProvider' }
      : name === './ExplorerRowLinks' ? { useExplorerLinkAvailability: () => ({ page: { relations: [], selected: null } }) }
        : name === './explorerSelectionState' ? { selectedPageRows: (_previous, _keys, next) => next } : { default() {} }));
  const footer = compile('ExplorerResultsFooter.tsx', name => base(name) ?? {});
  const workbench = compile('ExplorerWorkbench.tsx', name => base(name) ?? (name === './ExplorerResultsFooter' ? footer
    : name === './ExplorerWorkbenchHeader' ? { default: 'WorkbenchHeader' }
      : name === './ExplorerSelection' ? selection : {}));
  const picker = compile('ExplorerLinkObjectPicker.tsx', name => base(name) ?? (name === 'react-dom' ? { createPortal: (children, host) => jsx('Portal', { children, host }) }
    : name === './ExplorerObjectOverview' ? overview
      : name === './ExplorerLinkTargetTable' ? { default: 'TargetTable' }
        : name === './ExplorerResultsFooter' ? footer
          : name === '../../services/api' ? { queryExplorerPreview: () => new Promise(() => {}) }
            : name === '../../services/explorerProperties' ? { fetchExplorerProperties: async () => ({ properties: [] }) }
              : name === '../../services/explorerLayouts' ? { layoutPath: () => '/target/layout', readLayouts: async () => ({ personal: { layout: null }, default: { layout: null } }) }
                : name === '../../services/session' ? { getSessionState: () => ({ authenticated: true, locked: false, identity: 'overlay-test-user' }) }
                  : name === './useExplorerErrorToast' ? { default() {} } : {}));
  const body = jsx('OriginalWorkspaceChildren', {}), rootState = instance(), selectionState = instance(), pickerState = instance(), pickerBodyState = instance();
  const rows = [{ id: '1', name: 'Alice' }, { id: '2', name: 'Bob' }];
  const props = { schema: 'ts_ods', table: 'people', rows, columns: ['id', 'name'], dataColumns: [],
    metadata: { presentation: { displayName: 'People', groups: [] } }, rowKey: row => row.id, rowIdentity: row => ({ id: row.id }), rowVersion: () => '1',
    loading: false, locked: false, renderRelationships: () => jsx('LinkedRecords', {}) };
  const rootProps = { header: { source: { kind: 'backed', schema: 'ts_ods', table: 'people' }, label: 'People' }, children: body, locked: false };
  let rootTree, sourceTree, portal, closed = 0;
  const updateRoot = () => {
    rootTree = render(rootState, workbench.default, rootProps);
    context.ExplorerSelectionOverlayContext.current = rootTree.props.value;
    return rootTree;
  };
  const updateSelection = () => sourceTree = render(selectionState, selection.default, props);
  updateRoot(); updateSelection();
  const hostElement = { name: 'Original results host' };
  const hostNode = () => nodes(sourceTree).find(node => node.props?.['aria-label'] === 'Object results');
  const sourceNode = () => nodes(sourceTree).find(node => node.props?.className === 'flow-explorer-selection__source');
  const panelNode = () => nodes(sourceTree).find(node => node.type === 'aside');
  const fieldset = () => nodes(rootTree).find(node => node.type === 'fieldset');
  const overlay = () => context.ExplorerSelectionOverlayContext.current;
  const selectFirst = () => { nodes(sourceTree).find(node => node.type === 'Table').props.rowSelection.onChange(['1'], [rows[0]], { type: 'all' }); updateSelection(); };
  const mountPicker = () => {
    const child = render(pickerState, picker.default, { open: true, source: rootProps.header.source,
      sourceRow: { identity: { id: '1' }, key: '1', title: 'Alice' },
      relation: { id: '7:outgoing', relationId: '7', name: 'Works for', direction: 'outgoing', canLink: true,
        target: { kind: 'backed', schema: 'ts_ods', table: 'companies' } }, onClose: () => closed++, onCreated() {} });
    portal = child ? render(pickerBodyState, child.type, child.props) : null;
    return portal;
  };
  return { body, props, rootProps, footer, updateRoot, updateSelection, hostNode, sourceNode, panelNode, fieldset, overlay, hostElement, selectFirst,
    root: () => rootTree, selection: () => sourceTree, mountPicker, portal: () => portal, closed: () => closed,
    unmountPicker: () => unmount(pickerBodyState) };
}

test('Selection registers its existing results element without replacing the preview panel', () => {
  const ui = workspace(); ui.selectFirst();
  const panel = ui.panelNode(), callback = ui.hostNode().props.ref;
  callback(ui.hostElement); ui.updateRoot(); ui.updateSelection();
  assert.equal(ui.overlay().host, ui.hostElement); assert.equal(ui.hostNode().props.ref, callback);
  assert.equal(ui.panelNode().props.id, panel.props.id); assert.equal(ui.panelNode().props.className, panel.props.className);
  assert.equal(ui.root().props.children.props.children[1], ui.body);
  assert.equal(ui.hostNode().props.inert, undefined); assert.equal(ui.hostNode().props['aria-hidden'], undefined);
  callback(null); ui.updateRoot(); assert.equal(ui.overlay().host, null);
});

test('opening the picker portals into results and locks existing header, source rows and right preview in place', () => {
  const ui = workspace(); ui.selectFirst(); ui.hostNode().props.ref(ui.hostElement); ui.updateRoot();
  const panel = ui.panelNode(), sourceTable = nodes(ui.selection()).find(node => node.type === 'Table');
  const portal = ui.mountPicker(); ui.updateRoot(); ui.updateSelection();
  assert.equal(portal.type, 'Portal'); assert.equal(portal.props.host, ui.hostElement);
  assert.equal(portal.props.children.props['aria-label'], 'Link object selection');
  assert.equal(ui.fieldset().props.disabled, true); assert.equal(ui.fieldset().props.inert, '');
  assert.equal(ui.sourceNode().props.inert, ''); assert.equal(ui.sourceNode().props['aria-hidden'], true);
  assert.equal(ui.hostNode().props.inert, undefined); assert.equal(ui.hostNode().props['aria-hidden'], undefined);
  assert.equal(ui.panelNode().props.inert, ''); assert.equal(ui.panelNode().props.id, panel.props.id);
  assert.equal(ui.panelNode().props.className, panel.props.className); assert.equal(ui.panelNode().props.style, panel.props.style);
  assert.equal(nodes(ui.selection()).find(node => node.type === 'Table').props.dataSource, sourceTable.props.dataSource);
  assert.equal(ui.root().props.children.props.children[1], ui.body);
  assert.equal(nodes(ui.selection()).some(node => node.type === 'Drawer' || node.type === 'Modal'), false);
  ui.unmountPicker();
});

test('closing the portal restores the same source selection and unlocks the original toolbar', () => {
  const ui = workspace(); ui.selectFirst(); ui.hostNode().props.ref(ui.hostElement); ui.updateRoot(); ui.mountPicker(); ui.updateRoot(); ui.updateSelection();
  const layer = ui.portal().props.children, event = { key: 'Escape', preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; } };
  layer.props.onKeyDown(event); assert.equal(ui.closed(), 1); assert.equal(event.prevented, true); assert.equal(event.stopped, true);
  ui.unmountPicker(); ui.updateRoot(); ui.updateSelection();
  assert.equal(ui.fieldset().props.disabled, false); assert.equal(ui.fieldset().props.inert, undefined);
  assert.equal(ui.sourceNode().props.inert, undefined); assert.equal(ui.sourceNode().props['aria-hidden'], undefined);
  assert.equal(ui.panelNode().props.inert, undefined);
  assert.deepEqual(nodes(ui.selection()).find(node => node.type === 'Table').props.rowSelection.selectedRowKeys, ['1']);
  ui.rootProps.locked = true; ui.updateRoot(); assert.equal(ui.fieldset().props.disabled, true);
});

test('a picker waits for the results host instead of opening a second page or global dialog', () => {
  const ui = workspace(); assert.equal(ui.overlay().host, null); assert.equal(ui.mountPicker(), null);
  assert.equal(ui.overlay().active, false); assert.equal(ui.fieldset().props.disabled, false);
});

test('footer actions share the original pagination nav and keep existing range and page controls', () => {
  const ui = workspace(), actions = [jsx('CancelAction', {}), jsx('AddLinkAction', {})], resized = [];
  const props = { offset: 10, rowCount: 2, pageSize: 10, loading: false, canPageFurther: false,
    onPageSize: size => resized.push(size), onPrevious() {}, onNext() {}, children: actions };
  const result = ui.footer.ExplorerResultsFooter(props), nav = nodes(result).find(node => node.type === 'nav');
  assert.equal(nav.props['aria-label'], 'Preview pagination');
  const items = nav.props.children;
  assert.equal(items[0].props.className, 'flow-preview-range'); assert.equal(items[0].props.children, 'Rows 11–12');
  assert.equal(items[1].props.className, 'flow-preview-page-controls');
  const controls = items[1].props.children;
  assert.equal(controls.at(-1).props.className, 'flow-preview-page-actions'); assert.equal(controls.at(-1).props.children, actions);
  assert.equal(controls.at(-2).props.children, 'Next');
  const select = nodes(result).find(node => node.type === 'NativeSelect'); select.props.onChange({ target: { value: '20' } });
  assert.deepEqual(resized, [20]);
  const regular = ui.footer.ExplorerResultsFooter({ ...props, children: undefined });
  assert.equal(nodes(regular).some(node => node.props?.className === 'flow-preview-page-actions'), false);
  const locked = ui.footer.ExplorerResultsFooter({ ...props, locked: true });
  const lockedSelect = nodes(locked).find(node => node.type === 'NativeSelect'); assert.equal(lockedSelect.props.disabled, true);
  lockedSelect.props.onChange({ target: { value: '50' } }); assert.deepEqual(resized, [20]);
});
