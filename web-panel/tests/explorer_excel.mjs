import assert from 'node:assert/strict';
import { resolveObjectURL } from 'node:buffer';
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = ts.createSourceFile('ExplorerWorkbenchHeader.tsx', readFileSync(new URL('../src/pages/Pipeline/ExplorerWorkbenchHeader.tsx', import.meta.url), 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const functions = ['textValue', 'downloadExcel'].map(name => {
  const declaration = source.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === name);
  assert.ok(declaration, `Missing production function ${name}`);
  return declaration.getText(source);
}).join('\n');
const compiled = ts.transpileModule(functions + '\nexports.downloadExcel = downloadExcel;', {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const exports = {};
vm.runInThisContext('(function (require, exports, String, Number, Boolean) {\n' + compiled + '\n})')(
  createRequire(import.meta.url), exports, String, Number, Boolean,
);
const { downloadExcel } = exports;

async function captureDownloads(run) {
  const original = Object.getOwnPropertyDescriptor(globalThis, 'document'), downloads = [], removals = [];
  globalThis.document = {
    createElement(tag) {
      assert.equal(tag, 'a');
      let removed;
      const removal = new Promise(resolve => { removed = resolve; });
      return {
        style: {}, removed,
        click() {
          const blob = resolveObjectURL(this.href);
          assert.ok(blob instanceof Blob, 'The browser writer must download a real Blob');
          downloads.push({ fileName: this.download, blob });
        },
        removal,
      };
    },
    body: {
      appendChild(anchor) { removals.push(anchor.removal); },
      removeChild(anchor) { anchor.removed(); },
    },
  };
  try {
    await run(downloads);
    return downloads;
  } finally {
    await Promise.all(removals);
    if (original) Object.defineProperty(globalThis, 'document', original);
    else delete globalThis.document;
  }
}

async function inspectWorkbook(blob) {
  assert.equal(blob.type, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');
  const result = spawnSync('python3', ['-c', `
import io, json, sys, zipfile
import xml.etree.ElementTree as ET
ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
with zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read())) as archive:
    shared = ET.fromstring(archive.read('xl/sharedStrings.xml'))
    strings = [''.join(item.itertext()) for item in shared]
    workbook = ET.fromstring(archive.read('xl/workbook.xml'))
    sheet = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
    cells = {}
    for cell in sheet.findall('.//s:sheetData/s:row/s:c', ns):
        value = cell.find('s:v', ns)
        if value is not None:
            raw = value.text
            kind = cell.get('t', 'n')
            cells[cell.get('r')] = {'type': kind, 'value': strings[int(raw)] if kind == 's' else raw}
    print(json.dumps({
        'sheets': [item.get('name') for item in workbook.findall('s:sheets/s:sheet', ns)],
        'rows': len(sheet.findall('s:sheetData/s:row', ns)),
        'cells': cells,
        'formulas': len(sheet.findall('.//s:f', ns)),
    }, ensure_ascii=False))
`], { input: Buffer.from(await blob.arrayBuffer()), encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr || String(result.error));
  return JSON.parse(result.stdout);
}

test('Excel export produces an XLSX workbook with ordered fields and literal typed values', async () => {
  const fields = ['formula', 'id', 'leading', 'unicode', 'json', 'empty', 'flag', 'amount', 'largeNumber', 'missing'];
  const rows = [
    { formula: '=HYPERLINK("https://example.com")', id: '12345678901234567890', leading: '00042', unicode: '测试图片\n第二行', json: { tags: ['图片', 'pdf'], year: 2026 }, empty: null, flag: true, amount: 12.5, largeNumber: 1000000000000001, excluded: 'not exported' },
    { formula: '+SUM(1,2)', id: '2', leading: '03', unicode: '🙂', json: ['a', null], empty: null, flag: false, amount: -2.75, largeNumber: 1000000000000002 },
  ];
  const downloads = await captureDownloads(() => downloadExcel(rows, fields, '测试对象'));
  assert.equal(downloads.length, 1);
  assert.equal(downloads[0].fileName, '测试对象.xlsx');
  const { cells, sheets, rows: rowCount, formulas } = await inspectWorkbook(downloads[0].blob);
  assert.deepEqual(sheets, ['Results']);
  assert.equal(rowCount, 3);
  assert.equal(formulas, 0, 'Record strings must never become spreadsheet formulas');
  fields.forEach((field, column) => assert.deepEqual(cells[String.fromCharCode(65 + column) + '1'], { type: 's', value: field }));
  rows.forEach((row, index) => {
    const at = column => cells[column + (index + 2)];
    for (const [column, field] of [['A', 'formula'], ['B', 'id'], ['C', 'leading'], ['D', 'unicode']]) {
      assert.deepEqual(at(column), { type: 's', value: row[field] });
    }
    assert.deepEqual(at('E'), { type: 's', value: JSON.stringify(row.json) });
    assert.equal(at('F'), undefined);
    assert.deepEqual(at('G'), { type: 'b', value: row.flag ? '1' : '0' });
    assert.deepEqual(at('H'), { type: 'n', value: String(row.amount) });
    assert.deepEqual(at('I'), { type: 's', value: String(row.largeNumber) });
    assert.equal(at('J'), undefined);
    assert.equal(at('K'), undefined, 'Only the requested fields should be exported');
  });
});

test('Excel export writes exactly the supplied result subset', async () => {
  const downloads = await captureDownloads(() => downloadExcel([{ id: 'selected-2' }], ['id'], 'selection'));
  const workbook = await inspectWorkbook(downloads[0].blob);
  assert.equal(workbook.rows, 2);
  assert.deepEqual(workbook.cells.A2, { type: 's', value: 'selected-2' });
  assert.equal(workbook.cells.A3, undefined);
});

test('Excel cell character limits reject oversized values without downloading a partial workbook', async () => {
  const downloads = await captureDownloads(async observed => {
    await assert.rejects(() => downloadExcel([{ title: 'x'.repeat(32768) }], ['title'], 'oversized'), /32767|32,767/);
    assert.equal(observed.length, 0);
  });
  assert.equal(downloads.length, 0);
  const allowed = await captureDownloads(() => downloadExcel([{ title: 'x'.repeat(32767) }], ['title'], 'maximum'));
  const workbook = await inspectWorkbook(allowed[0].blob);
  assert.equal(workbook.cells.A2.value.length, 32767);
});
