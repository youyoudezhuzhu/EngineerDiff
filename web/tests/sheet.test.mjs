/*
 * 表格比较一致性：JS 移植版 vs 桌面版 Python 引擎。
 * 覆盖 XLSX（公式模式 / 缓存值模式）、合并单元格、仅单侧工作表、CSV 表格视图。
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..', '..');
const FIXTURES = path.join(ROOT, 'tests', 'fixtures');
const REFERENCE = path.join(HERE, 'reference', 'sheet_reference.py');

const require = createRequire(import.meta.url);
globalThis.XLSX = require(path.join(HERE, '..', 'vendor', 'xlsx.full.min.js'));

const { loadAnyFile } = await import('../js/workbook.js');
const { compareLoaded, setTextComparator } = await import('../js/sheetcompare.js');
const { diffTextFiles } = await import('../js/textdiff.js');

setTextComparator(diffTextFiles);

function fakeFile(buffer, name) {
  return {
    name,
    arrayBuffer: async () => buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength),
  };
}

async function loadSide(fileName) {
  const buffer = await fs.readFile(path.join(FIXTURES, fileName));
  return loadAnyFile(fakeFile(new Uint8Array(buffer), fileName));
}

function normalizeLocation(location) {
  if (!location) return null;
  return {
    sheet: location.sheet,
    row: location.row ?? null,
    col: location.col ?? null,
    text: location.text,
    kind: location.kind,
  };
}

function normalizeDiff(diff) {
  return {
    id: diff.id,
    kind: diff.kind,
    severity: diff.severity,
    title: diff.title,
    detail: diff.detail,
    left: normalizeLocation(diff.left),
    right: normalizeLocation(diff.right),
  };
}

const CASES = [
  { name: 'sample-xlsx', left: 'sample_left.xlsx', right: 'sample_right.xlsx', options: {} },
  { name: 'formula-mode', left: 'formula_left.xlsx', right: 'formula_right.xlsx', options: {} },
  {
    name: 'value-mode',
    left: 'formula_left.xlsx',
    right: 'formula_right.xlsx',
    options: { formulaMode: 'value' },
  },
  { name: 'sample-csv-table', left: 'sample_left.csv', right: 'sample_right.csv', options: {} },
];

test('spreadsheet compare matches desktop engine exactly', async () => {
  const payload = {
    cases: CASES.map((item) => ({
      name: item.name,
      left: path.join(FIXTURES, item.left),
      right: path.join(FIXTURES, item.right),
      options: item.options,
    })),
  };
  const expected = JSON.parse(
    execFileSync('python3', [REFERENCE], {
      input: JSON.stringify(payload),
      encoding: 'utf-8',
      maxBuffer: 64 * 1024 * 1024,
    }),
  ).cases;

  for (let i = 0; i < CASES.length; i += 1) {
    const item = CASES[i];
    const left = await loadSide(item.left);
    const right = await loadSide(item.right);
    const result = compareLoaded(left, right, { sheet: item.options, text: {} });
    const actual = {
      name: item.name,
      diffs: result.table.diffs.map(normalizeDiff),
      sheets: result.table.sheets.map((sheet) => ({ name: sheet.name, status: sheet.status })),
    };
    assert.deepEqual(actual, expected[i], `case ${item.name} differs from Python engine`);
  }
});
